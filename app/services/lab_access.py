"""Server-owned 3D lab catalog and per-card student access."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.lab_access import LabCardAccess, LabCardLevel, LabCardStudent, LabCardTag, LabCardTariff
from app.services.task_blocks import BlockAudience, BlockViewer, is_block_open_to
from app.config import settings


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    source = Path(__file__).resolve().parents[1] / "data" / "lab_catalog.json"
    return {card["id"]: card for card in json.loads(source.read_text(encoding="utf-8"))["cards"]}


def visible_cards() -> dict[str, dict]:
    return {key: card for key, card in catalog().items() if not card["hidden"]}


def audiences(db: Session) -> dict[str, BlockAudience]:
    ids = {row.card_id for row in db.query(LabCardAccess.card_id)}
    data = {key: {"tariffs": set(), "levels": set(), "user_ids": set(), "tag_ids": set()} for key in ids}
    for card_id, value in db.query(LabCardTariff.card_id, LabCardTariff.tariff):
        if card_id in data:
            data[card_id]["tariffs"].add(value)
    for card_id, value in db.query(LabCardLevel.card_id, LabCardLevel.level):
        if card_id in data:
            data[card_id]["levels"].add(value)
    for card_id, value in db.query(LabCardStudent.card_id, LabCardStudent.user_id):
        if card_id in data:
            data[card_id]["user_ids"].add(value)
    for card_id, value in db.query(LabCardTag.card_id, LabCardTag.tag_id):
        if card_id in data:
            data[card_id]["tag_ids"].add(value)
    return {key: BlockAudience(**{field: frozenset(values) for field, values in axes.items()}) for key, axes in data.items()}


def open_card_ids(db: Session, user: dict) -> set[str]:
    cards = visible_cards()
    if user.get("role_rank", 0) > 1:
        return set(cards)
    if user.get("role_rank") != 1:
        return set()
    viewer = BlockViewer(db, user_id=user.get("user_id"), tariff=user.get("tariff"))
    rules = audiences(db)
    return {
        key for key in cards
        if key in rules and is_block_open_to(rules[key], viewer)
    }


@lru_cache(maxsize=1)
def asset_owners() -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    for key, card in visible_cards().items():
        for asset in card["assets"]:
            owners.setdefault(asset, set()).add(key)
            if asset.lower().endswith(".gltf"):
                model_file = Path(settings.lab_assets_dir) / asset
                if not model_file.is_file():
                    continue
                try:
                    source = json.loads(model_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for buffer in source.get("buffers", []):
                    uri = buffer.get("uri", "")
                    if not uri or uri.startswith("data:") or "/" in uri or "\\" in uri or ".." in uri:
                        continue
                    if uri.lower().endswith(".bin"):
                        sibling = str(Path(asset).parent / uri).replace("\\", "/")
                        owners.setdefault(sibling, set()).add(key)
    return owners


def may_read_asset(asset_path: str, open_ids: set[str]) -> bool:
    """Shared files are readable when any owning card is open."""
    owners = asset_owners().get(asset_path)
    if owners is None:
        # Only previews and textures remain public to authenticated lab users.
        return not asset_path.lower().endswith((".gltf", ".glb", ".bin", ".mp4", ".webm"))
    return bool(owners & open_ids)
