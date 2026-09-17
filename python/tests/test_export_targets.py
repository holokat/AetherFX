import json
import zipfile
from pathlib import Path

import pytest

from aetherfx.studio.attachments import AttachmentError, AttachmentStore
from aetherfx.studio.export_targets import ExportError, export_to_target, describe_targets


def fake_call(tool, **args):
    assert tool == "export_effect"
    path = Path(args["path"])
    if args["format"] == "package":
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"format": "aetherfx-package", "version": 1}))
        (path / "runtime.json").write_text("{}")
        (path / "textures").mkdir()
        (path / "textures" / "tex.png").write_bytes(b"\x89PNG")
        return {"path": str(path), "files": ["manifest.json", "runtime.json"], "manifest": {"version": 1}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    return {"path": str(path), "files": [str(path)]}


def test_zip_download_when_no_destination(tmp_path: Path):
    info = export_to_target(fake_call, "Fire AOE", "package", None, tmp_path)
    assert info["download"].startswith("/api/file?path=")
    with zipfile.ZipFile(info["path"]) as zf:
        names = zf.namelist()
    assert "fire_aoe.aetherfx/manifest.json" in names and "fire_aoe.aetherfx/textures/tex.png" in names


def test_unreal_export_copies_into_content_and_installs_plugin(tmp_path: Path):
    project = tmp_path / "MyGame"
    project.mkdir()
    (project / "MyGame.uproject").write_text("{}")
    repo = tmp_path / "repo"
    (repo / "engines" / "unreal" / "AetherFX" / "Source").mkdir(parents=True)
    (repo / "engines" / "unreal" / "AetherFX" / "AetherFX.uplugin").write_text("{}")
    info = export_to_target(fake_call, "Fire AOE", "unreal", str(project), tmp_path, repo_root=repo)
    assert Path(info["path"]) == project / "Content" / "AetherFX" / "Effects" / "fire_aoe.aetherfx"
    assert (project / "Content" / "AetherFX" / "Effects" / "fire_aoe.aetherfx" / "runtime.json").exists()
    assert info["installed"] is True and (project / "Plugins" / "AetherFX" / "AetherFX.uplugin").exists()
    # second export overwrites the package but does not reinstall
    info2 = export_to_target(fake_call, "Fire AOE", "unreal", str(project), tmp_path, repo_root=repo)
    assert info2["installed"] is False


def test_unity_and_godot_markers(tmp_path: Path):
    unity = tmp_path / "U"; (unity / "Assets").mkdir(parents=True)
    info = export_to_target(fake_call, "Ice", "unity", str(unity), tmp_path)
    assert Path(info["path"]) == unity / "Assets" / "AetherFX" / "Effects" / "ice.aetherfx"
    godot = tmp_path / "G"; godot.mkdir(); (godot / "project.godot").write_text("")
    info = export_to_target(fake_call, "Ice", "godot", str(godot), tmp_path)
    assert Path(info["path"]) == godot / "aetherfx" / "effects" / "ice.aetherfx"
    with pytest.raises(ExportError):
        export_to_target(fake_call, "Ice", "unreal", str(godot), tmp_path)  # not an unreal project
    with pytest.raises(ExportError):
        export_to_target(fake_call, "Ice", "nope", None, tmp_path)
    assert {t["id"] for t in describe_targets()} == {"unreal", "unity", "godot", "package", "flipbook"}


def test_targets_say_honestly_what_they_deliver():
    targets = {t["id"]: t for t in describe_targets()}
    assert targets["unreal"]["maturity"] == "runtime"
    assert targets["unity"]["maturity"] == "data" and "Flipbook" in targets["unity"]["summary"]
    assert targets["godot"]["maturity"] == "data" and "Flipbook" in targets["godot"]["summary"]
    assert targets["package"]["maturity"] == targets["flipbook"]["maturity"] == "universal"
    assert all(t["badge"] and t["summary"] for t in targets.values())


def test_unreal_install_copies_the_prebuilt_libraries(tmp_path):
    from aetherfx.studio.export_targets import install_unreal_plugin, unreal_plugin_has_libs

    repo = tmp_path / "repo"
    plugin = repo / "engines" / "unreal" / "AetherFX"
    libs = plugin / "Source" / "ThirdParty" / "AetherFXLib" / "lib" / "Mac"
    libs.mkdir(parents=True)
    (plugin / "AetherFX.uplugin").write_text("{}")
    (libs / "libaetherfx_static.a").write_bytes(b"!<arch>")
    (plugin / "Intermediate").mkdir()
    (plugin / "Intermediate" / "junk.o").write_bytes(b"x")
    project = tmp_path / "MyGame"
    project.mkdir()

    assert install_unreal_plugin(project, repo) is True
    installed = project / "Plugins" / "AetherFX"
    assert unreal_plugin_has_libs(installed)                      # the plugin does not compile without them
    assert not (installed / "Intermediate").exists()              # build products stay behind
    assert install_unreal_plugin(project, repo) is False          # never overwrites an existing install


def test_attachment_store_round_trip(tmp_path: Path):
    from PIL import Image
    import io
    buf = io.BytesIO(); Image.new("RGB", (300, 200), (200, 30, 60)).save(buf, format="PNG")
    store = AttachmentStore(tmp_path / "att")
    info = store.save("my ref.png", buf.getvalue())
    assert info["width"] == 300 and info["name"] == "my_ref.png"
    assert store.path_for(info["id"]).exists() and store.thumb_for(info["id"]).exists()
    assert store.resolve([info["id"]]) == [str(Path(info["path"]).resolve())]
    with pytest.raises(AttachmentError):
        store.save("bad.txt", b"not an image")
    assert store.delete(info["id"]) is True and store.path_for(info["id"]) is None
    assert store.delete("nope") is False
