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

#: ``maturity`` says what the export gives you TODAY, so the UI never oversells a target:
#: "runtime" = plays in that engine through an AetherFX runtime; "data" = the files arrive but
#: nothing plays them yet; "universal" = works anywhere.
TARGETS: dict[str, dict[str, Any]] = {
    "unreal": {"label": "Unreal Engine", "marker": "*.uproject", "content": "Content/AetherFX/Effects",
               "hint": "the folder containing your .uproject", "maturity": "runtime", "badge": "UE",
               "summary": "Package plus the AetherFX plugin (UE 5.8): import the package and cast it from an actor."},
    "unity": {"label": "Unity", "marker": "Assets", "content": "Assets/AetherFX/Effects",
              "hint": "the Unity project root (contains Assets/)", "maturity": "data", "badge": "U",
              "summary": "Data package only for now (textures, meshes, runtime.json). The Unity runtime is not "
                         "built yet: use the Flipbook export to play the effect in Unity today."},
    "godot": {"label": "Godot", "marker": "project.godot", "content": "aetherfx/effects",
              "hint": "the folder containing project.godot", "maturity": "data", "badge": "G",
              "summary": "Data package only for now (textures, meshes, runtime.json). The Godot runtime is not "
                         "built yet: use the Flipbook export to play the effect in Godot today."},
    "package": {"label": "Package (.aetherfx)", "marker": None, "content": "", "hint": "any folder, or download a zip",
                "maturity": "universal", "badge": "PKG",
                "summary": "The engine-agnostic interchange package: resolved runtime data, baked textures, mesh "
                           "variants and previews (docs/PACKAGE_FORMAT.md)."},
    "flipbook": {"label": "Flipbook sprite sheet", "marker": None, "content": "", "hint": "any folder, or download",
                 "maturity": "universal", "badge": "2D",
                 "summary": "A sprite sheet plus a JSON manifest: plays in any engine or 2D framework."},
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


_UNREAL_LIB_DIR = Path("Source") / "ThirdParty" / "AetherFXLib" / "lib"


def unreal_plugin_has_libs(plugin_root: Path) -> bool:
    """True when the plugin carries the prebuilt engine libraries it links against."""
    lib_dir = plugin_root / _UNREAL_LIB_DIR
    if not lib_dir.is_dir():
        return False
    return any(lib_dir.rglob("*.a")) or any(lib_dir.rglob("*.lib"))


def install_unreal_plugin(project_root: Path, repo_root: Path) -> bool:
    """Copy engines/unreal/AetherFX into <Project>/Plugins if missing. Returns True when installed.

    The prebuilt engine libraries under ``Source/ThirdParty/AetherFXLib/lib`` are copied with it: the
    plugin links them statically and does not compile without them (they are produced by
    ``engines/unreal/scripts/sync_libs.sh`` and are not stored in git).
    """
    src = repo_root / "engines" / "unreal" / "AetherFX"
    dst = project_root / "Plugins" / "AetherFX"
    if not src.is_dir() or dst.exists():
        return False
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("Binaries", "Intermediate", "*.bak"))
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
        info["maturity"] = TARGETS[target]["maturity"]
        if target == "unreal" and repo_root is not None:
            info["installed"] = install_unreal_plugin(root, repo_root)
            plugin = root / "Plugins" / "AetherFX"
            info["plugin_ready"] = plugin.is_dir() and unreal_plugin_has_libs(plugin)
            info["note"] = ("Unreal imports the package through the AetherFX plugin importer (Content Browser: "
                            "right-click > Import, or reimport). " + ("Plugin installed into Plugins/AetherFX; "
                            "restart the editor once. " if info["installed"] else ""))
            if plugin.is_dir() and not info["plugin_ready"]:
                info["note"] += ("The plugin has no prebuilt engine libraries yet: run "
                                 "engines/unreal/scripts/sync_libs.sh in the AetherFX repository, then copy "
                                 "Source/ThirdParty/AetherFXLib/lib into the installed plugin.")
        elif target in ("unity", "godot"):
            info["note"] = TARGETS[target]["summary"]
        return info


def describe_targets() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "hint": v["hint"], "maturity": v["maturity"], "badge": v["badge"],
             "summary": v["summary"]} for k, v in TARGETS.items()]


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))


def read_settings(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
