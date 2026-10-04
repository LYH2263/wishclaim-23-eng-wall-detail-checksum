"""夹具：故意把一侧投影写歪，给门禁制造真实漂移。

模拟「投影器写坏/缓存过期未失效」的故障：只改 projection_cache 里指定
wish 指定通道的四个规范字段（标题/status/claimer/expires_at），源表
`wishes` 的认领业务字段一概不碰。
"""
from datetime import datetime, timezone

from app.integrity.projection import CANONICAL_FIELDS, CHANNELS, digest_of

SKEW_STATUS = "claimed"
SKEW_CLAIMER = "fixture-ghost"
SKEW_EXPIRES_AT = "1999-12-31T23:59:59+00:00"
SKEW_TITLE_SUFFIX = " [SKEWED]"


def pick_target(conn, wish_id: int | None = None) -> int:
    """Deterministic target: the given id, else the lowest-id open wish."""
    if wish_id is not None:
        return wish_id
    row = conn.execute(
        "SELECT id FROM wishes WHERE status='open' ORDER BY id LIMIT 1"
    ).fetchone() or conn.execute("SELECT id FROM wishes ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("no wishes to skew")
    return row["id"]


def skew(conn, wish_id: int | None = None, channel: str = "wall", now=None) -> dict:
    """Write one projection channel wrong on purpose. Returns what was skewed."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    wid = pick_target(conn, wish_id)
    row = conn.execute(
        "SELECT * FROM projection_cache WHERE channel=? AND wish_id=?",
        (channel, wid),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no {channel} projection for wish {wid}; run align first")

    fields = {k: row[k] for k in CANONICAL_FIELDS}
    fields["title"] = (fields["title"] or "") + SKEW_TITLE_SUFFIX
    fields["status"] = SKEW_STATUS
    fields["claimer"] = SKEW_CLAIMER
    fields["expires_at"] = SKEW_EXPIRES_AT
    ts = (now or datetime.now(timezone.utc)).isoformat()
    conn.execute(
        "UPDATE projection_cache"
        " SET title=?, status=?, claimer=?, expires_at=?, digest=?, projected_at=?"
        " WHERE channel=? AND wish_id=?",
        (fields["title"], fields["status"], fields["claimer"], fields["expires_at"],
         digest_of(fields), ts, channel, wid),
    )
    return {"wish_id": wid, "channel": channel, "fields": fields}
