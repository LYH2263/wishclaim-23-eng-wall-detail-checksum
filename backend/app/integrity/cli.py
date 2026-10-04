"""工程门禁 CLI：墙列表 vs 详情投影一致性校验。

用法：
  python -m app.integrity.cli check [--fix] [--report PATH]
  python -m app.integrity.cli skew [--wish-id N] [--channel wall|detail]
  python -m app.integrity.cli align

退出码：0 = 已对齐（或 --fix 修复后对齐）；1 = 检出漂移。
默认只报告；--fix 才做重投影修复，且只重写 projection_cache，
绝不篡改 wishes 表里的认领业务字段。
"""
import argparse
import json
import sys
from pathlib import Path

from app.db import connect
from app.integrity import projection
from app.integrity.check import apply_fix, run_check
from app.integrity.fixture import skew as skew_fixture
from app.integrity.report import Report

EXIT_OK = 0
EXIT_DRIFT = 1


def _open():
    conn = connect()
    projection.ensure_schema(conn)
    return conn


def _emit(report: Report, report_path: str | None) -> None:
    if report_path:
        Path(report_path).write_text(report.to_json() + "\n", encoding="utf-8")
    else:
        print(report.to_json())
    print(report.render_text(), file=sys.stderr)


def cmd_check(args) -> int:
    conn = _open()
    try:
        checked, drifts = run_check(conn)
        repaired = 0
        if drifts and args.fix:
            apply_fix(conn, drifts)
            conn.commit()
            repaired = len(drifts)
            # 修复后复检，退出码以复检结果为准。
            checked, drifts = run_check(conn)
        report = Report(checked=checked, entries=drifts,
                        fix_applied=bool(args.fix), repaired=repaired)
        _emit(report, args.report)
        return EXIT_OK if report.ok else EXIT_DRIFT
    finally:
        conn.close()


def cmd_skew(args) -> int:
    conn = _open()
    try:
        change = skew_fixture(conn, wish_id=args.wish_id, channel=args.channel)
        conn.commit()
        print(json.dumps({"skewed": change}, ensure_ascii=False, sort_keys=True))
        return EXIT_OK
    finally:
        conn.close()


def cmd_align(args) -> int:
    conn = _open()
    try:
        projection.reproject_all(conn)
        conn.commit()
        checked, drifts = run_check(conn)
        report = Report(checked=checked, entries=drifts)
        _emit(report, args.report)
        return EXIT_OK if report.ok else EXIT_DRIFT
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="integrity-gate",
        description="墙列表 vs 详情投影一致性工程门禁",
    )
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="校验投影一致性；默认只报告")
    c.add_argument("--fix", action="store_true",
                   help="重投影修复漂移行（只写 projection_cache，不动 wishes 业务字段）")
    c.add_argument("--report", metavar="PATH", help="JSON 报告输出路径（默认打到 stdout）")

    s = sub.add_parser("skew", help="夹具：故意写歪一侧投影（仅 projection_cache）")
    s.add_argument("--wish-id", type=int, default=None)
    s.add_argument("--channel", choices=list(projection.CHANNELS), default="wall")

    a = sub.add_parser("align", help="从 wishes 源表全量重投影")
    a.add_argument("--report", metavar="PATH", help="JSON 报告输出路径（默认打到 stdout）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "skew":
        return cmd_skew(args)
    if args.command == "align":
        return cmd_align(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
