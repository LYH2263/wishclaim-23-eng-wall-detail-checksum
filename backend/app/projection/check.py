"""投影一致性校验：对每个 wish 计算墙侧/详情侧投影字段校验和并对照。"""
from . import FIELDS, checksum


def _canonical_rows(c):
    return {r["id"]: dict(r) for r in c.execute(f"SELECT id, {', '.join(FIELDS)} FROM wishes")}


def _card_rows(c):
    return {r["wish_id"]: dict(r) for r in c.execute(
        f"SELECT wish_id, {', '.join(FIELDS)}, checksum FROM wall_cards")}


def diff_fields(detail_snap: dict, wall_snap: dict) -> list[str]:
    """逐字段列出不一致项；同值（含同为 NULL）不报。"""
    return [k for k in FIELDS if detail_snap.get(k) != wall_snap.get(k)]


def scan(c) -> list[dict]:
    """返回漂移明细。每条含 wish_id、两侧摘要、校验和、不一致字段。

    覆盖三类漂移：
      - value_drift : 字段值（status/claimer/expires_at/title）不一致
      - missing_card: 详情有该 wish，墙侧投影缺失
      - orphan_card : 墙侧有投影，详情侧 wish 已不存在
    另校验墙侧缓存的 checksum 是否与墙侧当前字段自洽（缓存损坏）。
    """
    detail = _canonical_rows(c)
    cards = _card_rows(c)
    drifts = []

    for wid in sorted(set(detail) | set(cards)):
        d = detail.get(wid)
        card = cards.get(wid)
        if d is None:
            drifts.append({
                "wish_id": wid,
                "kind": "orphan_card",
                "fields": list(FIELDS),
                "wall_snapshot": {k: card[k] for k in FIELDS},
                "detail_snapshot": None,
                "wall_checksum": card["checksum"],
                "detail_checksum": None,
            })
            continue
        if card is None:
            drifts.append({
                "wish_id": wid,
                "kind": "missing_card",
                "fields": list(FIELDS),
                "wall_snapshot": None,
                "detail_snapshot": {k: d[k] for k in FIELDS},
                "wall_checksum": None,
                "detail_checksum": checksum({k: d[k] for k in FIELDS}),
            })
            continue

        detail_snap = {k: d[k] for k in FIELDS}
        wall_snap = {k: card[k] for k in FIELDS}
        detail_cs = checksum(detail_snap)
        mismatched = diff_fields(detail_snap, wall_snap)
        card_self_ok = card["checksum"] == checksum(wall_snap)
        if mismatched or not card_self_ok:
            drifts.append({
                "wish_id": wid,
                "kind": "value_drift" if mismatched else "checksum_corrupt",
                "fields": mismatched,
                "wall_snapshot": wall_snap,
                "detail_snapshot": detail_snap,
                "wall_checksum": card["checksum"],
                "detail_checksum": detail_cs,
            })
    return drifts
