"""校验核心：墙卡投影 vs 详情投影的一致性对照。

For every wish the gate recomputes each channel's canonical-field checksum
and compares the two sides, plus a source-of-truth cross-check. It only reads
`wishes`; the optional fix path writes projection_cache exclusively.
"""
from dataclasses import dataclass, field

from app.integrity.projection import (
    CANONICAL_FIELDS,
    canonical_fields,
    digest_of,
    load_projections,
    reproject,
)

KIND_FIELD_MISMATCH = "field_mismatch"          # 墙侧与详情侧字段不一致
KIND_MISSING_WALL = "missing_wall_projection"   # 墙侧投影缺失
KIND_MISSING_DETAIL = "missing_detail_projection"  # 详情侧投影缺失
KIND_ORPHAN = "orphan_projection"               # 源表已无此 wish，投影残留
KIND_SOURCE_DIVERGE = "diverges_from_source"    # 两侧一致但都与源表背离


@dataclass
class Drift:
    wish_id: int
    kind: str
    wall_digest: str | None
    detail_digest: str | None
    # field -> {"wall": ..., "detail": ..., "source": ...}（仅列出不一致的字段）
    fields: dict = field(default_factory=dict)
    fixed: bool = False


def _diff_fields(a: dict, b: dict, source: dict | None = None) -> dict:
    out = {}
    for f in CANONICAL_FIELDS:
        if a.get(f) != b.get(f):
            entry = {"wall": a.get(f), "detail": b.get(f)}
            if source is not None:
                entry["source"] = source.get(f)
            out[f] = entry
    return out


def check_projections(conn) -> list[Drift]:
    """Compare wall-side vs detail-side projections for every wish."""
    wishes = {
        r["id"]: canonical_fields(r)
        for r in conn.execute("SELECT * FROM wishes")
    }
    projections = load_projections(conn)
    drifts: list[Drift] = []

    for wid in sorted(wishes):
        source = wishes[wid]
        chans = projections.get(wid, {})
        wall, detail = chans.get("wall"), chans.get("detail")
        wall_dg = digest_of(canonical_fields(wall)) if wall else None
        detail_dg = digest_of(canonical_fields(detail)) if detail else None

        if wall is None or detail is None:
            kind = KIND_MISSING_WALL if wall is None else KIND_MISSING_DETAIL
            drifts.append(Drift(wid, kind, wall_dg, detail_dg))
            continue

        wall_fields, detail_fields = canonical_fields(wall), canonical_fields(detail)
        if wall_fields != detail_fields:
            drifts.append(Drift(
                wid, KIND_FIELD_MISMATCH, wall_dg, detail_dg,
                fields=_diff_fields(wall_fields, detail_fields, source),
            ))
        elif wall_fields != source:
            # 两侧摘要一致但同源表背离：双写都坏了，单靠互相对照发现不了。
            drifts.append(Drift(
                wid, KIND_SOURCE_DIVERGE, wall_dg, detail_dg,
                fields={f: {"wall": wall_fields[f], "detail": detail_fields[f],
                            "source": source[f]}
                        for f in CANONICAL_FIELDS if wall_fields[f] != source[f]},
            ))

    for wid in sorted(set(projections) - set(wishes)):
        chans = projections[wid]
        wall, detail = chans.get("wall"), chans.get("detail")
        drifts.append(Drift(
            wid, KIND_ORPHAN,
            digest_of(canonical_fields(wall)) if wall else None,
            digest_of(canonical_fields(detail)) if detail else None,
        ))
    return drifts


def run_check(conn) -> tuple[int, list[Drift]]:
    """-> (checked_wish_count, drifts)"""
    checked = conn.execute("SELECT COUNT(*) c FROM wishes").fetchone()["c"]
    return checked, check_projections(conn)


def apply_fix(conn, drifts: list[Drift], now=None) -> None:
    """重投影修复：只重写漂移 wish 的投影行（或删除孤儿投影）。

    Hard rules:
    - writes projection_cache ONLY — `wishes` claim business fields
      (status/claimer/claimed_at/expires_at) are never touched;
    - wishes not present in `drifts` are left byte-for-byte intact.
    """
    for d in drifts:
        reproject(conn, d.wish_id, now=now)
        d.fixed = True
