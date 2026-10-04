"""投影门禁测试夹具（独立于用例文件）。

职责：造对齐库、故意写歪墙侧、重对齐、表快照、调用同一道门禁命令。
仅依赖标准库，便于在未安装 fastapi/pytest 的环境直接 `python -m unittest`。
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _env(db_path: Path) -> dict:
    env = dict(os.environ)
    env["WISHCLAIM_DB"] = str(db_path)
    env.pop("DATA_DIR", None)
    return env


def make_aligned_db(db_path: Path) -> None:
    """初始化结构与种子数据，墙卡投影与详情天然对齐。"""
    sys.path.insert(0, str(BACKEND))
    from app.seed import init_db
    os.environ["WISHCLAIM_DB"] = str(db_path)
    os.environ.pop("DATA_DIR", None)
    init_db()


def skew_wall_side(db_path: Path, wish_id: int, **wall_overrides) -> None:
    """故意写歪「墙侧」投影：改 wall_cards 字段并把校验和留成陈旧值。

    只动投影旁路，不碰 wishes。wall_overrides 可取 title/status/claimer/expires_at。
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sets = ", ".join(f"{k}=?" for k in wall_overrides)
    vals = list(wall_overrides.values()) + [wish_id]
    conn.execute(f"UPDATE wall_cards SET {sets}, checksum='stale0000stale00' WHERE wish_id=?", vals)
    conn.commit()
    conn.close()


def corrupt_wall_checksum(db_path: Path, wish_id: int) -> None:
    """只把墙侧缓存的校验和写坏，字段值保持一致（校验和缓存损坏场景）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE wall_cards SET checksum='deadbeefdeadbeef' WHERE wish_id=?", (wish_id,))
    conn.commit()
    conn.close()


def realign(db_path: Path) -> None:
    """夹具侧重对齐：以 wishes 为真相重新投影每一张墙卡（等价于一次干净重投影）。"""
    sys.path.insert(0, str(BACKEND))
    from app.db import connect
    from app.projection import upsert_card
    os.environ["WISHCLAIM_DB"] = str(db_path)
    c = connect()
    for r in c.execute("SELECT id FROM wishes"):
        upsert_card(c, r["id"])
    c.commit()
    c.close()


def snapshot(db_path: Path, table: str) -> dict:
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = {r[0]: tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")}
    conn.close()
    return rows


def first_wish_id(db_path: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(db_path)
    wid = conn.execute("SELECT MIN(id) FROM wishes").fetchone()[0]
    conn.close()
    return wid


def run_gate(db_path: Path, *extra_args) -> subprocess.CompletedProcess:
    """以子进程运行同一道门禁命令（测试关注的就是真实退出码与报告文本）。"""
    return subprocess.run(
        [sys.executable, "-m", "app.projection", "--db", str(db_path), *extra_args],
        cwd=str(BACKEND), env=_env(db_path), capture_output=True, text=True,
    )
