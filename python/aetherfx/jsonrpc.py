"""Newline-delimited JSON-RPC 2.0 transports for the AetherFX engine CLI.

The C++ CLI (``aetherfx serve``) speaks JSON-RPC 2.0 over stdin/stdout, one
UTF-8 JSON object per line; stderr carries logs only.  This module owns the
framing, the request/response correlation and the mapping of JSON-RPC errors
onto :class:`AetherError`.

Reading guide for an LLM:

* :class:`Transport` is the protocol every higher layer depends on.  It has
  exactly two operations, ``request(method, params) -> dict`` and ``close()``.
* :class:`SubprocessTransport` spawns the real engine binary.
* :class:`InMemoryTransport` dispatches to Python callables and is what the
  test-suite (and the MCP server's offline mode) uses.  It round-trips through
  the very same envelope-building and error-mapping code as the subprocess
  transport, so framing bugs surface in unit tests.

Wire format (mirrored exactly by ``src/cli``)::

    -->  {"jsonrpc":"2.0","id":1,"method":"create_effect","params":{"name":"x"}}
    <--  {"jsonrpc":"2.0","id":1,"result":{"effect_id":"e0"}}
    <--  {"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"...",
          "data":{"aether_code":"E004","node":"flames","param":"rate"}}}
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "JSONRPC_VERSION",
    "ERROR_METHOD_NOT_FOUND",
    "ERROR_INVALID_PARAMS",
    "ERROR_INTERNAL",
    "AetherError",
    "TransportError",
    "Transport",
    "SubprocessTransport",
    "InMemoryTransport",
    "build_request",
    "parse_response",
]

JSONRPC_VERSION = "2.0"

#: Standard JSON-RPC 2.0 error codes the engine uses.
ERROR_PARSE = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603

JsonDict = dict[str, Any]

#: Maximum number of out-of-band messages (notifications, stale replies) that
#: :meth:`SubprocessTransport.request` will skip before giving up.
_MAX_SKIPPED_MESSAGES = 64

#: How many stderr lines to keep for diagnostics when the engine dies.
_STDERR_TAIL_LINES = 40


class AetherError(RuntimeError):
    """A structured error returned by the engine.

    ``code`` is the JSON-RPC code (-32601 unknown method, -32602 invalid params
    or any ``aether::Error``, -32603 internal).  The optional fields come from
    the error object's ``data`` member and are all optional on the wire:

    * ``aether_code`` - a validation code from docs/VOCABULARY.md, e.g. ``E004``
      (unknown parameter) or ``W003`` (particle budget high).
    * ``node`` - the node id the error is about.
    * ``param`` - the parameter name the error is about.
    """

    def __init__(
        self,
        code: int,
        message: str,
        aether_code: str | None = None,
        node: str | None = None,
        param: str | None = None,
        data: JsonDict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.aether_code = aether_code
        self.node = node
        self.param = param
        self.data: JsonDict = data or {}

    def __str__(self) -> str:
        parts = [self.message]
        detail = [
            f"{key}={value}"
            for key, value in (
                ("aether_code", self.aether_code),
                ("node", self.node),
                ("param", self.param),
            )
            if value is not None
        ]
        detail.append(f"rpc_code={self.code}")
        parts.append("(" + ", ".join(detail) + ")")
        return " ".join(parts)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"AetherError(code={self.code!r}, message={self.message!r}, "
            f"aether_code={self.aether_code!r}, node={self.node!r}, param={self.param!r})"
        )


class TransportError(RuntimeError):
    """The transport itself failed: process death, bad framing, unusable binary.

    This is deliberately distinct from :class:`AetherError`, which means "the
    engine understood the request and rejected it".
    """


@runtime_checkable
class Transport(Protocol):
    """Anything that can send one JSON-RPC request and return its result."""

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> JsonDict:
        """Send ``method`` with ``params`` and return the ``result`` object.

        Raises :class:`AetherError` if the engine answered with an error object
        and :class:`TransportError` if the message could not be exchanged.
        """
        ...

    def close(self) -> None:
        """Release the transport.  Safe to call more than once."""
        ...


def build_request(request_id: int | str, method: str, params: Mapping[str, Any] | None) -> JsonDict:
    """Build a JSON-RPC 2.0 request envelope.

    ``params`` is always sent as an object (never omitted) because every engine
    tool takes a - possibly empty - argument object.
    """
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
        "params": dict(params or {}),
    }


def parse_response(message: Mapping[str, Any], method: str) -> JsonDict:
    """Turn a decoded response envelope into a result dict or raise.

    ``method`` is only used to make error messages actionable.
    """
    if "error" in message and message["error"] is not None:
        error = message["error"]
        if not isinstance(error, Mapping):
            raise TransportError(f"{method}: malformed JSON-RPC error member: {error!r}")
        data = error.get("data")
        data_dict: JsonDict = dict(data) if isinstance(data, Mapping) else {}
        raise AetherError(
            code=int(error.get("code", ERROR_INTERNAL)),
            message=str(error.get("message", "unknown engine error")),
            aether_code=data_dict.get("aether_code"),
            node=data_dict.get("node"),
            param=data_dict.get("param"),
            data=data_dict,
        )
    if "result" not in message:
        raise TransportError(f"{method}: response has neither 'result' nor 'error': {message!r}")
    result = message["result"]
    if result is None:
        return {}
    if not isinstance(result, Mapping):
        raise TransportError(f"{method}: 'result' must be a JSON object, got {type(result).__name__}")
    return dict(result)


class SubprocessTransport:
    """Run ``aetherfx serve`` as a child process and talk JSON-RPC over its stdio.

    Parameters
    ----------
    binary:
        Path to the ``aetherfx`` executable.
    output_dir:
        Passed through as ``--output-dir DIR``; the engine writes rendered
        frames and saved effects there.
    extra_args:
        Extra CLI arguments appended after ``serve`` (and after
        ``--output-dir``), for experiments and future flags.

    The transport is synchronous and serialises concurrent callers with a lock,
    so a single instance is safe to share between threads.
    """

    def __init__(
        self,
        binary: str | os.PathLike[str],
        output_dir: str | os.PathLike[str] | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.binary = str(binary)
        self.output_dir = str(output_dir) if output_dir is not None else None
        self.extra_args: tuple[str, ...] = tuple(extra_args)
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        argv = [self.binary, "serve"]
        if self.output_dir is not None:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            argv += ["--output-dir", self.output_dir]
        argv += list(self.extra_args)
        self.argv: tuple[str, ...] = tuple(argv)

        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv is built from explicit inputs
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise TransportError(f"cannot start engine binary {self.binary!r}: {exc}") from exc

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="aetherfx-stderr", daemon=True
        )
        self._stderr_thread.start()

    # -- diagnostics ------------------------------------------------------

    def _drain_stderr(self) -> None:
        """Keep the child's stderr pipe empty and remember the last lines."""
        stream = self._proc.stderr
        if stream is None:  # pragma: no cover - always a pipe here
            return
        try:
            for line in stream:
                self._stderr_tail.append(line.rstrip("\n"))
        except (ValueError, OSError):  # pragma: no cover - pipe closed during shutdown
            pass

    def stderr_tail(self) -> str:
        """Return the last engine log lines, for inclusion in error messages."""
        return "\n".join(self._stderr_tail)

    def _death_error(self, method: str, detail: str) -> TransportError:
        code = self._proc.poll()
        tail = self.stderr_tail()
        message = (
            f"{method}: engine process {' '.join(self.argv)!r} {detail}"
            f" (exit code {code!r})"
        )
        if tail:
            message += f"\n--- engine stderr (last {len(self._stderr_tail)} lines) ---\n{tail}"
        return TransportError(message)

    # -- Transport protocol ------------------------------------------------

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run."""
        return self._closed

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> JsonDict:
        """Send one request and block until its matching response arrives."""
        with self._lock:
            if self._closed:
                raise TransportError(f"{method}: transport is closed")
            if self._proc.poll() is not None:
                raise self._death_error(method, "is not running")

            self._next_id += 1
            request_id = self._next_id
            payload = json.dumps(build_request(request_id, method, params), ensure_ascii=False)

            stdin = self._proc.stdin
            stdout = self._proc.stdout
            if stdin is None or stdout is None:  # pragma: no cover - always pipes here
                raise TransportError(f"{method}: engine stdio pipes are unavailable")
            try:
                stdin.write(payload + "\n")
                stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as exc:
                raise self._death_error(method, f"closed its stdin while writing ({exc})") from exc

            for _ in range(_MAX_SKIPPED_MESSAGES):
                try:
                    line = stdout.readline()
                except (ValueError, OSError) as exc:
                    raise self._death_error(method, f"closed its stdout while reading ({exc})") from exc
                if line == "":
                    raise self._death_error(method, "exited before answering")
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TransportError(
                        f"{method}: engine wrote a non-JSON line on stdout: {line[:200]!r} ({exc})"
                    ) from exc
                if not isinstance(message, dict):
                    raise TransportError(f"{method}: engine wrote a non-object message: {line[:200]!r}")
                if message.get("id") != request_id:
                    # A notification or a stale reply; skip it and keep reading.
                    continue
                return parse_response(message, method)

            raise TransportError(
                f"{method}: no response with id {request_id} after "
                f"{_MAX_SKIPPED_MESSAGES} out-of-band messages"
            )

    def close(self) -> None:
        """Ask the engine to shut down, then make sure the process is gone."""
        if self._closed:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self.request("shutdown", {})
                except (AetherError, TransportError):
                    # The engine is allowed to exit without answering shutdown.
                    pass
        finally:
            self._closed = True
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (ValueError, OSError):  # pragma: no cover
                        pass
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                    self._proc.kill()
                    self._proc.wait(timeout=5)
                except OSError:  # pragma: no cover
                    pass

    def __enter__(self) -> "SubprocessTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class InMemoryTransport:
    """A :class:`Transport` backed by Python callables - no subprocess.

    ``handlers`` maps a method name to ``handler(params: dict) -> dict``.  A
    handler may raise :class:`AetherError` to produce a JSON-RPC error member.
    ``tools/list``, ``ping`` and ``shutdown`` are provided by default and can be
    overridden by supplying handlers with those names.

    The call still goes through :func:`build_request` / :func:`parse_response`,
    so this transport exercises the real envelope and error-mapping code.
    """

    def __init__(
        self,
        handlers: Mapping[str, Callable[[JsonDict], Any]] | None = None,
        tools: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.handlers: dict[str, Callable[[JsonDict], Any]] = dict(handlers or {})
        self.tools: list[JsonDict] = [dict(tool) for tool in tools]
        #: Every ``(method, params)`` pair seen, in order - handy in assertions.
        self.calls: list[tuple[str, JsonDict]] = []
        self._next_id = 0
        self._closed = False

    # -- built-in methods --------------------------------------------------

    def _builtin_tools_list(self, _params: JsonDict) -> JsonDict:
        """Default ``tools/list``.

        When :attr:`tools` was supplied it *is* the advertised list, so a fake
        engine can model a binary that implements more methods than it
        advertises.  Otherwise the list is derived from the handler names.
        """
        if self.tools:
            return {"tools": [dict(tool) for tool in self.tools]}
        listed: list[JsonDict] = []
        for name in self.handlers:
            if "/" in name or name in {"ping", "shutdown"}:
                continue
            listed.append(
                {
                    "name": name,
                    "description": f"{name} (in-memory fake)",
                    "input_schema": {"type": "object", "additionalProperties": True},
                    "mutating": False,
                    "category": "fake",
                }
            )
        return {"tools": listed}

    def _dispatch(self, method: str, params: JsonDict) -> Any:
        handler = self.handlers.get(method)
        if handler is not None:
            return handler(params)
        if method == "tools/list":
            return self._builtin_tools_list(params)
        if method == "ping":
            return {"ok": True}
        if method == "shutdown":
            self._closed = True
            return {"ok": True}
        raise AetherError(ERROR_METHOD_NOT_FOUND, f"unknown method: {method}")

    # -- Transport protocol ------------------------------------------------

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run."""
        return self._closed

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> JsonDict:
        """Dispatch to a handler, wrapping the outcome in a JSON-RPC envelope."""
        self._next_id += 1
        request = build_request(self._next_id, method, params)
        request_params: JsonDict = request["params"]
        self.calls.append((method, request_params))

        response: JsonDict = {"jsonrpc": JSONRPC_VERSION, "id": request["id"]}
        try:
            result = self._dispatch(method, request_params)
        except AetherError as exc:
            data: JsonDict = dict(exc.data)
            for key, value in (
                ("aether_code", exc.aether_code),
                ("node", exc.node),
                ("param", exc.param),
            ):
                if value is not None:
                    data[key] = value
            error: JsonDict = {"code": exc.code, "message": exc.message}
            if data:
                error["data"] = data
            response["error"] = error
        else:
            response["result"] = result if result is not None else {}

        # Round-trip through JSON so handlers cannot smuggle non-serialisable
        # objects past the fake transport that the real one would reject.
        decoded = json.loads(json.dumps(response, ensure_ascii=False))
        return parse_response(decoded, method)

    def close(self) -> None:
        """Mark the transport closed.  Idempotent."""
        self._closed = True

    def __enter__(self) -> "InMemoryTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
