"""重投影修复：只重写 wall_cards 投影旁路，绝不触碰 wishes 业务字段。

安全边界（由测试守护）：
  1. 修复只用详情侧快照 upsert wall_cards；wishes 表零写入。
  2. 仅重写检出漂移的 wish_id；已对齐（干净）的行不执行任何 UPDATE。
  3. 孤儿卡（wishes 中已不存在的投影）删除——这仍只动投影表。
"""
from . import upsert_card
from .check import scan


def repair(c) -> dict:
    before = {r["wish_id"]: dict(r) for r in c.execute(
        "SELECT wish_id, title, status, claimer, expires_at, checksum FROM wall_cards")}
    wishes_snapshot = {r["id"]: dict(r) for r in c.execute(
        "SELECT id, title, note, status, claimer, claimed_at, expires_at, data_quality FROM wishes")}

    drifts = scan(c)
    touched = []
    for d in drifts:
        wid = d["wish_id"]
        if d["kind"] == "orphan_card":
            c.execute("DELETE FROM wall_cards WHERE wish_id=?", (wid,))
        else:
            upsert_card(c, wid)  # 取值源固定为 wishes 行，外部无法注入字段
        touched.append(wid)

    after = {r["wish_id"]: dict(r) for r in c.execute(
        "SELECT wish_id, title, status, claimer, expires_at, checksum FROM wall_cards")}
    # 干净行（未出现在 drift 列表中）投影内容必须逐字节不变
    untouched_ok = all(
        before[wid] == after[wid]
        for wid in before.keys() & after.keys() - set(touched)
    )

    return {
        "repaired_ids": touched,
        "touched_count": len(touched),
        "clean_rows_unchanged": untouched_ok,
        "wishes_before": wishes_snapshot,
    }
