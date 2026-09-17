#!/usr/bin/env python3
"""Rebuild ``community/index.json`` from ``community/effects/*.json``.

The index is what the studio's Community view and any static host (GitHub Pages,
a CDN) read: one small JSON document with the name, description, credit, tags,
duration, node count, controls, the sha256 of every effect file and the names of
the previews CI rendered for it.

Output is deterministic - same inputs, same bytes - so CI can rebuild it and
commit only when it really changed.

    python tools/build_community_index.py                      # repo defaults
    python tools/build_community_index.py --previews out/previews
    python tools/build_community_index.py --check              # CI: fail if stale

Previews are never committed: pass ``--previews`` pointing at the directory CI
rendered them into, and the index records their file names for the published
site.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from aetherfx.community import build_index, index_text, write_index  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("effects_dir", nargs="?", default=str(REPO_ROOT / "community" / "effects"),
                        help="directory of contributed effects (default: community/effects)")
    parser.add_argument("--out", default=None, help="index path (default: <effects_dir>/../index.json)")
    parser.add_argument("--previews", default=None, metavar="DIR",
                        help="record the preview files found in DIR (they are never committed)")
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit 1 when the file on disk is out of date")
    args = parser.parse_args(argv)

    effects_dir = Path(args.effects_dir)
    target = Path(args.out) if args.out else effects_dir.parent / "index.json"
    index = build_index(effects_dir, previews_dir=args.previews)
    text = index_text(index)

    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print(f"{target} is out of date; run tools/build_community_index.py", file=sys.stderr)
            return 1
        print(f"{target} is up to date ({index['count']} effect(s))")
        return 0

    write_index(index, target)
    print(f"wrote {target} ({index['count']} effect(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
