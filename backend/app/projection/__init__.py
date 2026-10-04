"""墙卡只读投影旁路。

详情侧以 ``wishes`` 表为 canonical 真相；墙侧列表读取投影表 ``wall_cards``。
本包只维护投影，任何修复逻辑都不得写 ``wishes`` 的认领业务字段。
"""
import hashlib
import json

# 参与两侧投影一致性校验的字段（顺序即报告顺序）
FIELDS = ("status", "claimer", "expires_at", "title")

PROJECTION_DDL = """
CREATE TABLE IF NOT EXISTS wall_cards(
  wish_id    INTEGER PRIMARY KEY,
  title      TEXT,
  status     TEXT,
  claimer    TEXT,
  expires_at TEXT,
  checksum   TEXT
);
"""


def create_projection(c) -> None:
    c.executescript(PROJECTION_DDL)


def project(row) -> dict:
    """从任意 sqlite 行投影出受校验字段。"""
    return {k: row[k] for k in FIELDS}


def checksum(snap: dict) -> str:
    """投影字段校验和：canonical JSON + sha256，截断 16 位足够行级比对。"""
    blob = json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def upsert_card(c, wish_id: int) -> None:
    """以 wishes 行为唯一真相，重写某一行的墙卡投影（含校验和）。

    只写 wall_cards，不接收任何外部传入的字段值，杜绝借投影修复篡改业务字段。
    """
    r = c.execute(
        f"SELECT id, {', '.join(FIELDS)} FROM wishes WHERE id=?", (wish_id,)
    ).fetchone()
    if r is None:
        raise ValueError(f"wish {wish_id} 不存在，无法投影")
    snap = project(r)
    c.execute(
        """
        INSERT INTO wall_cards(wish_id, title, status, claimer, expires_at, checksum)
        VALUES (:wish_id, :title, :status, :claimer, :expires_at, :checksum)
        ON CONFLICT(wish_id) DO UPDATE SET
          title      = excluded.title,
          status     = excluded.status,
          claimer    = excluded.claimer,
          expires_at = excluded.expires_at,
          checksum   = excluded.checksum
        """,
        {
            "wish_id": wish_id,
            "title": snap["title"],
            "status": snap["status"],
            "claimer": snap["claimer"],
            "expires_at": snap["expires_at"],
            "checksum": checksum(snap),
        },
    )
