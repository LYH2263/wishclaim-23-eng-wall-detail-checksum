"""墙列表/详情投影一致性工程门禁测试。

同一道门禁命令 `python -m app.projection --db ...` 子进程验证：
  - 夹具故意写歪一侧 → 退出码非 0，报告含 wish_id 与双侧摘要；
  - 对齐后同一命令连跑两次 → 退出码 0、报告空；
  - 默认只报告，漂移库不被修改；
  - --repair 只重写投影旁路：认领业务字段与干净行均不动。
"""
import json
import tempfile
import unittest
from pathlib import Path

from app.tests import projection_fixtures as fx


class ProjectionGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "wishclaim.db"
        fx.make_aligned_db(self.db)
        self.wid = fx.first_wish_id(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- 检出：写歪一侧 → 非 0 + 双侧摘要 ----

    def assert_skew_detected(self, **wall_overrides):
        fx.skew_wall_side(self.db, self.wid, **wall_overrides)
        r = fx.run_gate(self.db)
        self.assertNotEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn(f"wish_id={self.wid}", r.stdout)
        self.assertIn("墙侧摘要", r.stdout)
        self.assertIn("详情侧摘要", r.stdout)
        for key in wall_overrides:
            self.assertIn(key, r.stdout)
        return r

    def test_detect_status_drift(self):
        self.assert_skew_detected(status="fulfilled")

    def test_detect_claimer_drift(self):
        self.assert_skew_detected(claimer="ghost")

    def test_detect_expires_at_drift(self):
        self.assert_skew_detected(expires_at="2020-01-01T00:00:00+00:00")

    def test_detect_title_drift(self):
        self.assert_skew_detected(title="被墙侧改歪的标题")

    def test_detect_stale_checksum_only(self):
        fx.corrupt_wall_checksum(self.db, self.wid)
        r = fx.run_gate(self.db)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("校验和", r.stdout)

    def test_json_report_shape(self):
        fx.skew_wall_side(self.db, self.wid, status="claimed", claimer="x")
        r = fx.run_gate(self.db, "--json")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["drift_count"], 1)
        d = payload["drifts"][0]
        self.assertEqual(d["wish_id"], self.wid)
        self.assertIn("status", d["fields"])
        self.assertIn("claimer", d["fields"])
        self.assertIsNotNone(d["wall_snapshot"])
        self.assertIsNotNone(d["detail_snapshot"])
        self.assertNotEqual(d["wall_checksum"], d["detail_checksum"])

    # ---- 对齐：同一命令连跑两次，0 且报告空 ----

    def test_aligned_command_passes_twice_with_empty_report(self):
        for _ in range(2):
            r = fx.run_gate(self.db)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            payload = json.loads(fx.run_gate(self.db, "--json").stdout)
            self.assertEqual(payload["drift_count"], 0)
            self.assertEqual(payload["drifts"], [])
            self.assertIn("无漂移", r.stdout)

    def test_skew_then_realign_then_passes(self):
        fx.skew_wall_side(self.db, self.wid, status="released")
        self.assertNotEqual(fx.run_gate(self.db).returncode, 0)
        fx.realign(self.db)
        r1 = fx.run_gate(self.db)
        r2 = fx.run_gate(self.db)  # 同一命令再跑一次
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r2.returncode, 0)
        self.assertIn("无漂移", r1.stdout)

    # ---- 默认只报告：漂移库不被改 ----

    def test_report_only_never_writes(self):
        fx.skew_wall_side(self.db, self.wid, status="claimed")
        wishes_before = fx.snapshot(self.db, "wishes")
        cards_before = fx.snapshot(self.db, "wall_cards")
        r = fx.run_gate(self.db)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(fx.snapshot(self.db, "wishes"), wishes_before)
        self.assertEqual(fx.snapshot(self.db, "wall_cards"), cards_before)

    # ---- --repair：只重写投影旁路 ----

    def test_repair_rewrites_projection_and_passes(self):
        fx.skew_wall_side(self.db, self.wid, status="claimed", claimer="ghost",
                          expires_at="2099-01-01T00:00:00+00:00", title="歪标题")
        self.assertNotEqual(fx.run_gate(self.db).returncode, 0)

        wishes_before = fx.snapshot(self.db, "wishes")
        clean_cards_before = {
            wid: row for wid, row in fx.snapshot(self.db, "wall_cards").items() if wid != self.wid
        }

        r = fx.run_gate(self.db, "--repair")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("干净行未改动: True", r.stdout)

        # 认领业务表零改动：status/claimer/claimed_at/expires_at/title/note 全保持
        self.assertEqual(fx.snapshot(self.db, "wishes"), wishes_before)

        cards_after = fx.snapshot(self.db, "wall_cards")
        # 干净行逐字节不变
        for wid, row in clean_cards_before.items():
            self.assertEqual(cards_after[wid], row, f"干净行 wish {wid} 被修复误伤")
        # 漂移行已按详情侧重投影
        self.assertEqual(json.loads(fx.run_gate(self.db, "--json").stdout)["drift_count"], 0)
        # 修复具有幂等性：再跑一次 --repair 不再触碰任何行
        r2 = fx.run_gate(self.db, "--repair")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("重投影 0 行", r2.stdout)

    def test_repair_json_reports_touched_ids_only(self):
        fx.skew_wall_side(self.db, self.wid, status="claimed")
        payload = json.loads(fx.run_gate(self.db, "--repair", "--json").stdout)
        self.assertEqual(payload["drift_count"], 0)
        self.assertEqual(payload["repair"]["repaired_ids"], [self.wid])
        self.assertTrue(payload["repair"]["clean_rows_unchanged"])

    def test_repair_does_not_backfill_corrupt_business_fields(self):
        """修复的字段来源必须固定为 wishes：即便墙侧写了认领人，也不得反向写回业务表。"""
        fx.skew_wall_side(self.db, self.wid, claimer="attacker", status="claimed")
        wishes_before = fx.snapshot(self.db, "wishes")
        fx.run_gate(self.db, "--repair")
        self.assertEqual(fx.snapshot(self.db, "wishes"), wishes_before)
        repaired = fx.snapshot(self.db, "wall_cards")[self.wid]
        # 投影已回到详情侧真相（该 wish 业务上仍 open、claimer 为空）
        import sqlite3
        conn = sqlite3.connect(self.db)
        truth = conn.execute("SELECT status, claimer FROM wishes WHERE id=?", (self.wid,)).fetchone()
        card = conn.execute("SELECT status, claimer FROM wall_cards WHERE wish_id=?", (self.wid,)).fetchone()
        conn.close()
        self.assertEqual(tuple(truth), tuple(card))
        self.assertNotIn("attacker", tuple(card))


if __name__ == "__main__":
    unittest.main()
