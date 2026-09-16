"""SubprocessTransport against a scripted stand-in for ``aetherfx serve``.

The real C++ CLI is not required: a tiny Python script speaks the same
newline-delimited JSON-RPC protocol, which exercises process spawn, framing,
stderr draining, error mapping, process death and shutdown for real.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from aetherfx.client import Client
from aetherfx.jsonrpc import ERROR_INVALID_PARAMS, AetherError, SubprocessTransport, TransportError

STUB_ENGINE = r'''#!/usr/bin/env python3
"""A stand-in for `aetherfx serve`: newline-delimited JSON-RPC 2.0 over stdio."""
import json, sys

argv = sys.argv[1:]
output_dir = None
if "--output-dir" in argv:
    output_dir = argv[argv.index("--output-dir") + 1]
print("stub engine: ready", file=sys.stderr, flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params") or {}
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "ping":
        response["result"] = {"ok": True}
    elif method == "shutdown":
        response["result"] = {"ok": True}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        break
    elif method == "tools/list":
        response["result"] = {"tools": [
            {"name": "create_effect", "description": "stub", "input_schema": {"type": "object"},
             "mutating": True, "category": "effect"}]}
    elif method == "echo_output_dir":
        response["result"] = {"output_dir": output_dir, "argv": argv}
    elif method == "notify_first":
        # A notification arrives before the real answer; the client must skip it.
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "progress",
                                     "params": {"pct": 50}}) + "\n")
        sys.stdout.write("\n")  # a blank line must be tolerated too
        response["result"] = {"ok": True}
    elif method == "log_then_answer":
        print("stub engine: working", file=sys.stderr, flush=True)
        response["result"] = {"ok": True}
    elif method == "boom":
        response["error"] = {"code": -32602, "message": "unknown parameter: rate",
                             "data": {"aether_code": "E004", "node": "flames", "param": "rate"}}
    elif method == "die":
        print("stub engine: fatal", file=sys.stderr, flush=True)
        sys.exit(3)
    elif method == "garbage":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        continue
    else:
        response["error"] = {"code": -32601, "message": "unknown method: %s" % method}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
'''


@pytest.fixture
def stub_binary(tmp_path: Path) -> Path:
    """Write the stub engine plus an executable ``aetherfx`` wrapper.

    The wrapper is a ``/bin/sh`` script rather than a shebanged Python file
    because the interpreter path may contain spaces, which a kernel shebang
    line does not handle.
    """
    script = tmp_path / "stub_engine.py"
    script.write_text(STUB_ENGINE, encoding="utf-8")
    path = tmp_path / "aetherfx"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def transport(stub_binary: Path) -> SubprocessTransport:
    handle = SubprocessTransport(stub_binary)
    try:
        yield handle
    finally:
        handle.close()


class TestFraming:
    def test_ping(self, transport: SubprocessTransport) -> None:
        assert transport.request("ping") == {"ok": True}

    def test_sequential_requests_keep_their_ids(self, transport: SubprocessTransport) -> None:
        for _ in range(5):
            assert transport.request("ping") == {"ok": True}

    def test_notifications_and_blank_lines_are_skipped(self, transport: SubprocessTransport) -> None:
        assert transport.request("notify_first") == {"ok": True}
        assert transport.request("ping") == {"ok": True}

    def test_stderr_is_logs_only(self, transport: SubprocessTransport) -> None:
        transport.request("log_then_answer")
        transport.request("ping")
        assert "stub engine: ready" in transport.stderr_tail()

    def test_non_json_stdout_is_a_transport_error(self, transport: SubprocessTransport) -> None:
        with pytest.raises(TransportError, match="non-JSON line"):
            transport.request("garbage")


class TestArguments:
    def test_serve_is_the_subcommand(self, stub_binary: Path) -> None:
        handle = SubprocessTransport(stub_binary)
        try:
            assert handle.argv[1] == "serve"
        finally:
            handle.close()

    def test_output_dir_is_passed_and_created(self, stub_binary: Path, tmp_path: Path) -> None:
        out = tmp_path / "renders"
        handle = SubprocessTransport(stub_binary, output_dir=out)
        try:
            assert handle.request("echo_output_dir")["output_dir"] == str(out)
            assert out.is_dir()
        finally:
            handle.close()

    def test_extra_args_are_appended(self, stub_binary: Path) -> None:
        handle = SubprocessTransport(stub_binary, extra_args=("--verbose",))
        try:
            assert handle.request("echo_output_dir")["argv"][-1] == "--verbose"
        finally:
            handle.close()


class TestErrors:
    def test_engine_error_maps_to_aether_error(self, transport: SubprocessTransport) -> None:
        with pytest.raises(AetherError) as excinfo:
            transport.request("boom")
        error = excinfo.value
        assert error.code == ERROR_INVALID_PARAMS
        assert (error.aether_code, error.node, error.param) == ("E004", "flames", "rate")

    def test_unknown_method_maps_to_method_not_found(self, transport: SubprocessTransport) -> None:
        with pytest.raises(AetherError) as excinfo:
            transport.request("nope")
        assert excinfo.value.code == -32601

    def test_process_death_is_reported_with_stderr(self, transport: SubprocessTransport) -> None:
        with pytest.raises(TransportError) as excinfo:
            transport.request("die")
        message = str(excinfo.value)
        assert "exited before answering" in message
        assert "stub engine: fatal" in message

    def test_a_dead_process_reports_its_exit_code(self, transport: SubprocessTransport) -> None:
        with pytest.raises(TransportError):
            transport.request("die")
        with pytest.raises(TransportError, match="exit code 3"):
            transport.request("ping")

    def test_a_missing_binary_fails_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(TransportError, match="cannot start engine binary"):
            SubprocessTransport(tmp_path / "does_not_exist")


class TestShutdown:
    def test_close_shuts_the_process_down(self, stub_binary: Path) -> None:
        handle = SubprocessTransport(stub_binary)
        handle.request("ping")
        handle.close()
        assert handle.closed is True
        assert handle._proc.poll() is not None  # noqa: SLF001 - asserting the lifecycle

    def test_close_is_idempotent(self, stub_binary: Path) -> None:
        handle = SubprocessTransport(stub_binary)
        handle.close()
        handle.close()

    def test_requests_after_close_are_rejected(self, stub_binary: Path) -> None:
        handle = SubprocessTransport(stub_binary)
        handle.close()
        with pytest.raises(TransportError, match="closed"):
            handle.request("ping")

    def test_context_manager(self, stub_binary: Path) -> None:
        with SubprocessTransport(stub_binary) as handle:
            assert handle.request("ping")["ok"] is True
        assert handle.closed is True


class TestThroughTheClient:
    def test_client_drives_the_stub_engine(self, stub_binary: Path, tmp_path: Path) -> None:
        with Client(binary=stub_binary, output_dir=tmp_path / "out") as engine:
            assert engine.available() is True
            assert engine.tool_names() == ["create_effect"]
            with pytest.raises(AetherError):
                engine.call("boom")

    def test_environment_variable_is_honoured(
        self, stub_binary: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AETHERFX_BINARY", str(stub_binary))
        with Client() as engine:
            assert engine.binary == str(stub_binary)
            assert engine.ping() == {"ok": True}
