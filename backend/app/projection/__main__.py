"""投影一致性门禁 CLI。

用法:
    python -m app.projection            # 只报告；有漂移退出码 1，无漂移 0
    python -m app.projection --json     # 机读报告
    python -m app.projection --repair   # 显式拍板开启：重投影修复后再校验
    python -m app.projection --db PATH  # 指定 sqlite 文件（默认取 DATA_DIR）

默认只报告；--repair 仅重写 wall_cards 投影旁路/缓存，不写 wishes 业务字段。
"""
import argparse
import json
import sys

from app.db import connect
from app.seed import init_db
from app.projection.check import scan
from app.projection.report import render_json, render_text
from app.projection.repair import repair


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.projection",
                                description="墙列表/详情投影一致性门禁")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告")
    p.add_argument("--repair", action="store_true",
                   help="重投影修复（只写 wall_cards，不动 wishes 业务字段）")
    p.add_argument("--db", help="sqlite 数据库路径（默认使用 DATA_DIR）")
    args = p.parse_args(argv)

    if args.db:
        import os
        os.environ["WISHCLAIM_DB"] = os.path.abspath(args.db)

    init_db()  # 确保两表存在；不影响已有数据
    c = connect()
    try:
        create_if_missing(c)
        if args.repair:
            info = repair(c)
            c.commit()
        drifts = scan(c)
        code = 0 if not drifts else 1

        if args.json:
            out = json.loads(render_json(drifts))
            if args.repair:
                out["repair"] = {
                    "touched_count": info["touched_count"],
                    "repaired_ids": info["repaired_ids"],
                    "clean_rows_unchanged": info["clean_rows_unchanged"],
                }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            if args.repair:
                print(f"[repair] 重投影 {info['touched_count']} 行: {info['repaired_ids']}；"
                      f"干净行未改动: {info['clean_rows_unchanged']}")
            print(render_text(drifts), end="")
        return code
    finally:
        c.close()


def create_if_missing(c) -> None:
    from app.projection import create_projection
    create_projection(c)
    # 投影首次上线：对缺失卡片静默补齐，使初始库自然对齐
    from app.projection import upsert_card
    ids = [r["id"] for r in c.execute("SELECT id FROM wishes")]
    have = {r["wish_id"] for r in c.execute("SELECT wish_id FROM wall_cards")}
    for wid in ids:
        if wid not in have:
            upsert_card(c, wid)
    c.commit()


if __name__ == "__main__":
    sys.exit(main())
