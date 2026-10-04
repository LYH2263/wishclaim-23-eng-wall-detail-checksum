"""墙列表 vs 详情投影一致性门禁的验收测试。

stdlib-only：本地 `python3 -m unittest` 与容器内 `pytest` 均可运行。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved_data_dir = os.environ.get("DATA_DIR")
        self.addCleanup(self._restore_env)
        os.environ["DATA_DIR"] = self.tmp.name
        from app import seed
        seed.init_db()

    def _restore_env(self):
        if self._saved_data_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._saved_data_dir

    def connect(self):
        from app.db import connect
        return connect()

    def run_cli(self, *argv) -> int:
        from app.integrity import cli
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(argv))

    def snapshot(self, table, order_by):
        c = self.connect()
        rows = [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY {order_by}")]
        c.close()
        return rows

    def wishes_snapshot(self):
        return self.snapshot("wishes", "id")

    def cache_snapshot(self):
        return self.snapshot("projection_cache", "channel, wish_id")

    def report_path(self) -> str:
        return str(Path(self.tmp.name) / "report.json")

    def read_report(self, path=None) -> dict:
        return json.loads(Path(path or self.report_path()).read_text(encoding="utf-8"))


class TestGate(GateTestBase):
    def test_fresh_db_aligned_check_twice_clean(self):
        """对齐后同一命令连跑两次：退出码 0 且报告为空。"""
        for _ in range(2):
            rc = self.run_cli("check", "--report", self.report_path())
            self.assertEqual(rc, 0)
            report = self.read_report()
            self.assertTrue(report["ok"])
            self.assertEqual(report["entries"], [])
            self.assertEqual(report["checked"], 4)  # seed 四条样例

    def test_skewed_fixture_trips_gate(self):
        """夹具故意写歪一侧 → 退出码非 0，报告含 wish_id 与双侧摘要。"""
        before = self.wishes_snapshot()
        rc = self.run_cli("skew", "--channel", "wall")
        self.assertEqual(rc, 0)
        # 夹具只允许写投影缓存，不得动业务表。
        self.assertEqual(self.wishes_snapshot(), before)

        rc = self.run_cli("check", "--report", self.report_path())
        self.assertEqual(rc, 1)
        report = self.read_report()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["entries"]), 1)
        entry = report["entries"][0]
        self.assertEqual(entry["wish_id"], 1)  # 夹具默认挑最小 id 的 open 愿望
        self.assertEqual(entry["kind"], "field_mismatch")
        # 报告必须带墙侧摘要与详情侧摘要，且二者不同。
        self.assertTrue(entry["wall_digest"])
        self.assertTrue(entry["detail_digest"])
        self.assertNotEqual(entry["wall_digest"], entry["detail_digest"])
        # 标题/status/claimer/expires_at 四处漂移都要被点出来。
        self.assertEqual(
            set(entry["fields"]),
            {"title", "status", "claimer", "expires_at"},
        )
        self.assertEqual(entry["fields"]["claimer"]["wall"], "fixture-ghost")
        self.assertIsNone(entry["fields"]["claimer"]["detail"])

    def test_fix_restores_alignment_and_preserves_business_table(self):
        """--fix 重投影修复：退出码归零，wishes 业务表逐字节不动。"""
        before = self.wishes_snapshot()
        self.run_cli("skew")
        rc = self.run_cli("check", "--fix", "--report", self.report_path())
        self.assertEqual(rc, 0)
        report = self.read_report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["entries"], [])
        self.assertTrue(report["fix_applied"])
        self.assertEqual(report["repaired"], 1)
        self.assertEqual(self.wishes_snapshot(), before)
        # 修复后再连跑两次，均为 0 且报告空。
        for _ in range(2):
            self.assertEqual(self.run_cli("check", "--report", self.report_path()), 0)
            self.assertEqual(self.read_report()["entries"], [])

    def test_fix_leaves_clean_projection_rows_untouched(self):
        """干净行不被改：只有漂移 wish 的投影行被重写。"""
        self.run_cli("skew", "--wish-id", "2", "--channel", "detail")
        before = self.cache_snapshot()
        rc = self.run_cli("check", "--fix")
        self.assertEqual(rc, 0)
        after = self.cache_snapshot()

        def key(row):
            return (row["channel"], row["wish_id"])

        before_by_key = {key(r): r for r in before}
        after_by_key = {key(r): r for r in after}
        self.assertEqual(set(before_by_key), set(after_by_key))
        for k, old in before_by_key.items():
            if k[1] == 2:
                continue  # 漂移 wish 允许被重写
            # 其余行连 projected_at 都不许变 —— 证明没有被重写。
            self.assertEqual(after_by_key[k], old, f"clean row rewritten: {k}")
        # 漂移 wish 的两个通道确实被重投影（projected_at 已更新）。
        self.assertNotEqual(after_by_key[("wall", 2)], before_by_key[("wall", 2)])
        self.assertNotEqual(after_by_key[("detail", 2)], before_by_key[("detail", 2)])

    def test_missing_projection_detected(self):
        c = self.connect()
        c.execute("DELETE FROM projection_cache WHERE channel='wall' AND wish_id=1")
        c.commit(); c.close()
        rc = self.run_cli("check", "--report", self.report_path())
        self.assertEqual(rc, 1)
        entry = self.read_report()["entries"][0]
        self.assertEqual(entry["kind"], "missing_wall_projection")
        self.assertIsNone(entry["wall_digest"])
        self.assertTrue(entry["detail_digest"])
        self.assertEqual(self.run_cli("check", "--fix"), 0)

    def test_orphan_projection_detected_and_repaired(self):
        before = self.wishes_snapshot()
        c = self.connect()
        for channel in ("wall", "detail"):
            c.execute(
                "INSERT INTO projection_cache"
                " (channel, wish_id, title, status, claimer, expires_at, digest, projected_at)"
                " VALUES (?, 999, 'ghost', 'open', NULL, NULL, 'x', '2026-01-01T00:00:00+00:00')",
                (channel,),
            )
        c.commit(); c.close()
        rc = self.run_cli("check", "--report", self.report_path())
        self.assertEqual(rc, 1)
        kinds = {e["kind"] for e in self.read_report()["entries"]}
        self.assertEqual(kinds, {"orphan_projection"})
        self.assertEqual(self.run_cli("check", "--fix"), 0)
        self.assertEqual(self.wishes_snapshot(), before)
        remaining = [r for r in self.cache_snapshot() if r["wish_id"] == 999]
        self.assertEqual(remaining, [])

    def test_source_divergence_detected_even_when_channels_agree(self):
        """两侧被同样写歪（互相对照不出）时，源表交叉校验必须检出。"""
        c = self.connect()
        c.execute(
            "UPDATE projection_cache SET status='fulfilled'"
            " WHERE wish_id=1"
        )
        c.commit(); c.close()
        rc = self.run_cli("check", "--report", self.report_path())
        self.assertEqual(rc, 1)
        entry = self.read_report()["entries"][0]
        self.assertEqual(entry["kind"], "diverges_from_source")
        self.assertEqual(entry["wall_digest"], entry["detail_digest"])
        self.assertEqual(entry["fields"]["status"]["source"], "open")
        self.assertEqual(self.run_cli("check", "--fix"), 0)


class TestGateProcessExitCodes(GateTestBase):
    """真实进程级退出码：skew → 非 0；对齐后同一命令连跑两次 0 且报告空。"""

    def run_proc(self, *argv) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "app.integrity.cli", *argv],
            cwd=BACKEND_ROOT, env=dict(os.environ),
            capture_output=True, text=True,
        )

    def test_exit_codes_end_to_end(self):
        report = self.report_path()

        skew = self.run_proc("skew", "--channel", "detail")
        self.assertEqual(skew.returncode, 0, skew.stderr)

        bad = self.run_proc("check", "--report", report)
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertEqual(len(self.read_report(report)["entries"]), 1)

        fixed = self.run_proc("check", "--fix", "--report", report)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)

        for _ in range(2):
            again = self.run_proc("check", "--report", report)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(self.read_report(report)["entries"], [])


if __name__ == "__main__":
    unittest.main()
