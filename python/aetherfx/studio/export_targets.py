"""Export an effect straight into a game project (or a zip) — the "Export to Unreal /
Unity / Godot" buttons of the studio.

Every target starts from the engine-agnostic interchange package
(``export_effect(format="package")``, see docs/PACKAGE_FORMAT.md) and copies it
to where the engine expects content:

* unreal:  <Project>/Content/AetherFX/Effects/<slug>.aetherfx/   (+ installs the plugin from
           engines/unreal/AetherFX into <Project>/Plugins/AetherFX on first use, when present)
* unity:   <Project>/Assets/AetherFX/Effects/<slug>.aetherfx/     (PNG/OBJ import natively; runtime.json is a TextAsset)
* godot:   <Project>/aetherfx/effects/<slug>.aetherfx/            (PNG/OBJ import natively)
* package: a zip in <output_dir>/exports (download) or a folder at `destination`
* flipbook: sprite sheet + manifest (universal, 2D)

`destination` is the project root (must contain a .uproject / Assets/ / project.godot
respectively). Without a destination a zip is produced for download.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

ToolCall = Callable[..., Any]  # call(tool_name, **args) -> dict

TARGETS: dict[str, dict[str, Any]] = {
    "unreal": {"label": "Unreal Engine", "marker": "*.uproject", "content": "Content/AetherFX/Effects",
               "hint": "the folder containing your .uproject"},
    "unity": {"label": "Unity", "marker": "Assets", "content": "Assets/AetherFX/Effects",
              "hint": "the Unity project root (contains Assets/)"},
    "godot": {"label": "Godot", "marker": "project.godot", "content": "aetherfx/effects",
              "hint": "the folder containing project.godot"},
    "package": {"label": "Package (.aetherfx)", "marker": None, "content": "", "hint": "any folder, or download a zip"},
    "flipbook": {"label": "Flipbook sprite sheet", "marker": None, "content": "", "hint": "any folder, or download"},
}


class ExportError(ValueError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "effect").lower()).strip("_")
    return slug or "effect"


def _project_root(target: str, destination: str | None) -> Path | None:
    if not destination:
        return None
    root = Path(destination).expanduser()
    if not root.is_dir():
        raise ExportError(f"destination folder does not exist: {root}")
    marker = TARGETS[target]["marker"]
    if marker is None:
        return root
    found = list(root.glob(marker)) if "*" in marker else [root / marker]
    if not found or not found[0].exists():
        raise ExportError(f"{root} does not look like a {TARGETS[target]['label']} project ({TARGETS[target]['hint']})")
    return root


def _zip_dir(src: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(src.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=str(Path(src.name) / file.relative_to(src)))
    return zip_path


def install_unreal_plugin(project_root: Path, repo_root: Path) -> bool:
    """Copy engines/unreal/AetherFX into <Project>/Plugins if missing. Returns True when installed."""
    src = repo_root / "engines" / "unreal" / "AetherFX"
    dst = project_root / "Plugins" / "AetherFX"
    if not src.is_dir() or dst.exists():
        return False
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("Binaries", "Intermediate", "lib", "*.bak"))
    return True


def export_to_target(call: ToolCall, effect_name: str, target: str, destination: str | None,
                     output_dir: Path, repo_root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the export for `target`. `call` is the engine tool call (name, **args) -> dict."""
    if target not in TARGETS:
        raise ExportError(f"unknown export target '{target}'; choose one of {', '.join(TARGETS)}")
    options = dict(options or {})
    slug = slugify(effect_name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(output_dir)
    exports_dir = output_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    if target == "flipbook":
        dest_dir = _project_root(target, destination) or exports_dir
        path = dest_dir / f"{slug}_flipbook.png"
        result = call("export_effect", format="flipbook", path=str(path), options=options)
        return {"target": target, "path": result.get("path", str(path)), "files": result.get("files", []),
                "download": None if destination else f"/api/file?path={path}"}

    # 1) the interchange package into a temp dir
    with tempfile.TemporaryDirectory(prefix="aetherfx-export-") as tmp:
        package_dir = Path(tmp) / f"{slug}.aetherfx"
        result = call("export_effect", format="package", path=str(package_dir), options=options)
        if not package_dir.is_dir():
            raise ExportError("package export produced no directory")
        manifest = result.get("manifest", {})
        info: dict[str, Any] = {"target": target, "effect": effect_name, "slug": slug, "manifest": manifest,
                                "files": result.get("files", [])}

        root = _project_root(target, destination)
        if root is None:
            zip_path = _zip_dir(package_dir, exports_dir / f"{slug}-{target}-{stamp}.zip")
            info.update({"path": str(zip_path), "download": f"/api/file?path={zip_path}", "installed": False})
            return info

        # 2) copy into the project's content folder
        content = root / TARGETS[target]["content"] if TARGETS[target]["content"] else root
        content.mkdir(parents=True, exist_ok=True)
        final = content / package_dir.name
        if final.exists():
            shutil.rmtree(final)
        shutil.copytree(package_dir, final)
        info.update({"path": str(final), "download": None, "installed": False})
        if target == "unreal" and repo_root is not None:
            info["installed"] = install_unreal_plugin(root, repo_root)
            info["note"] = ("Unreal imports the package through the AetherFX plugin importer (Content Browser: "
                            "right-click > Import, or reimport). " + ("Plugin installed into Plugins/AetherFX; "
                            "restart the editor once." if info["installed"] else ""))
        elif target == "unity":
            info["note"] = "Unity imports the PNG textures and OBJ meshes natively; runtime.json is available as a TextAsset for the AetherFX Unity runtime."
        elif target == "godot":
            info["note"] = "Godot imports the PNG/OBJ natively; runtime.json is read by the AetherFX Godot runtime."
        return info


def describe_targets() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "hint": v["hint"]} for k, v in TARGETS.items()]


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))


def read_settings(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
