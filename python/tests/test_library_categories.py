"""The Library groups built-in effects by category (server.CATEGORIES, effect_category)."""

from __future__ import annotations

import json
from pathlib import Path

from aetherfx.studio.server import CATEGORIES, effect_category

REPO_ROOT = Path(__file__).resolve().parents[2]


def doc(tags: list[str], category: str | None = None) -> dict:
    metadata: dict = {"tags": tags}
    if category is not None:
        metadata["category"] = category
    return {"metadata": metadata}


def test_tags_decide_most_specific_first() -> None:
    assert effect_category(doc(["ambient", "lantern", "light"])) == "ambient"
    assert effect_category(doc(["melee", "impact", "critical"])) == "melee"
    assert effect_category(doc(["buff", "shield", "support", "target"])) == "support"
    assert effect_category(doc(["heal", "target"])) == "support"
    assert effect_category(doc(["teleport", "utility"])) == "mobility"
    assert effect_category(doc(["blink", "mobility", "arcane"])) == "mobility"
    assert effect_category(doc(["fire", "meteor", "aoe", "projectile"])) == "aoe"   # area beats projectile
    assert effect_category(doc(["fire", "projectile", "bolt"])) == "targeted"
    assert effect_category(doc(["channel", "drain", "target"])) == "targeted"
    assert effect_category({}) == "targeted"


def test_an_explicit_category_wins_and_an_unknown_one_is_ignored() -> None:
    assert effect_category(doc(["void", "nebula"], "aoe")) == "aoe"
    assert effect_category(doc(["heal"], " Melee ")) == "melee"
    assert effect_category(doc(["heal"], "spells")) == "support"


def test_every_shipped_effect_lands_in_a_category_and_all_are_used() -> None:
    ids = {cid for cid, _ in CATEGORIES}
    used = set()
    for path in sorted((REPO_ROOT / "examples" / "effects").glob("*.json")):
        document = json.loads(path.read_text())
        if ((document.get("metadata") or {}).get("library") or {}).get("hidden"):
            continue
        category = effect_category(document)
        assert category in ids, path.name
        used.add(category)
    assert used == ids
    assert effect_category(json.loads((REPO_ROOT / "examples/effects/void_nebula.json").read_text())) == "aoe"
