"""Projection cache (投影旁路/缓存) fed from the wishes source of truth.

The wall list and the detail page each keep a denormalized projection of the
same canonical field set, stored as two channels ("wall" / "detail") of one
cache table. The integrity gate (app.integrity.check) audits that the two
channels never drift apart.

This module is the ONLY writer of projection_cache. It never mutates the
`wishes` table — claim business fields are owned by app.main / claim_lock.
"""
import hashlib
import json
from datetime import datetime, timezone

CHANNELS = ("wall", "detail")

# Fields the gate holds both projections accountable for:
# 标题 / status / claimer / expires_at.
CANONICAL_FIELDS = ("title", "status", "claimer", "expires_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_cache(
  channel TEXT NOT NULL,
  wish_id INTEGER NOT NULL,
  title TEXT,
  status TEXT,
  claimer TEXT,
  expires_at TEXT,
  digest TEXT NOT NULL,
  projected_at TEXT NOT NULL,
  PRIMARY KEY(channel, wish_id)
);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)


def canonical_fields(row) -> dict:
    """Project a wishes row (or cache row) down to the canonical field set."""
    return {k: row[k] for k in CANONICAL_FIELDS}


def digest_of(fields: dict) -> str:
    """Stable checksum of one projection's canonical fields."""
    blob = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_iso(now=None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _upsert(conn, channel, wish_id, fields, digest, projected_at):
    conn.execute(
        "INSERT INTO projection_cache"
        " (channel, wish_id, title, status, claimer, expires_at, digest, projected_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(channel, wish_id) DO UPDATE SET"
        " title=excluded.title, status=excluded.status, claimer=excluded.claimer,"
        " expires_at=excluded.expires_at, digest=excluded.digest,"
        " projected_at=excluded.projected_at",
        (channel, wish_id, fields["title"], fields["status"], fields["claimer"],
         fields["expires_at"], digest, projected_at),
    )


def reproject(conn, wish_id, now=None):
    """Rebuild both channels' projection rows for one wish from the source of truth.

    Writes projection_cache only. If the wish no longer exists, its stale
    projection rows are deleted (orphan repair).
    """
    row = conn.execute("SELECT * FROM wishes WHERE id=?", (wish_id,)).fetchone()
    if row is None:
        conn.execute("DELETE FROM projection_cache WHERE wish_id=?", (wish_id,))
        return
    fields = canonical_fields(row)
    digest = digest_of(fields)
    ts = _now_iso(now)
    for channel in CHANNELS:
        _upsert(conn, channel, wish_id, fields, digest, ts)


def reproject_all(conn, now=None):
    """Full realignment: re-project every wish and drop orphan cache rows."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM wishes")]
    for wid in ids:
        reproject(conn, wid, now=now)
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM projection_cache WHERE wish_id NOT IN ({marks})", ids)
    else:
        conn.execute("DELETE FROM projection_cache")


def load_projections(conn) -> dict:
    """-> {wish_id: {channel: cache_row_dict}}"""
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT * FROM projection_cache"):
        out.setdefault(r["wish_id"], {})[r["channel"]] = dict(r)
    return out
