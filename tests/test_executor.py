import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tmax import executor


def _cfg(tmp, **kw):
    base = {
        "enabled": True, "dry_run": False, "max_stake": 10.0,
        "max_exposure": 200.0, "price_min": 0.95, "price_max": 0.995,
        "kill_file": str(Path(tmp) / "NO_KILL"),
        "ledger": str(Path(tmp) / "ledger.json"), "chain_id": 137,
    }
    base.update(kw)
    return base


class ExecutorGuardTests(unittest.TestCase):
    def test_disabled_rejects(self):
        with TemporaryDirectory() as tmp:
            r = executor.place_no_order(
                "TID", 0.96, 5.0, ref="a", day="2026-08-07",
                cfg=_cfg(tmp, enabled=False))
            self.assertEqual(r["status"], "rejected")

    def test_price_out_of_band_rejects(self):
        with TemporaryDirectory() as tmp:
            for price in (0.94, 0.999):
                r = executor.place_no_order(
                    "TID", price, 5.0, ref="a", day="d", cfg=_cfg(tmp))
                self.assertEqual(r["status"], "rejected", price)

    def test_over_max_stake_rejects(self):
        with TemporaryDirectory() as tmp:
            r = executor.place_no_order(
                "TID", 0.96, 50.0, ref="a", day="d",
                cfg=_cfg(tmp, max_stake=10.0))
            self.assertEqual(r["status"], "rejected")

    def test_kill_switch_rejects(self):
        with TemporaryDirectory() as tmp:
            kill = Path(tmp) / "STOP"
            kill.write_text("x", encoding="utf-8")
            r = executor.place_no_order(
                "TID", 0.96, 5.0, ref="a", day="d",
                cfg=_cfg(tmp, kill_file=str(kill)))
            self.assertEqual(r["status"], "rejected")

    def test_dry_run_records_without_submitting(self):
        with TemporaryDirectory() as tmp:
            calls = []
            original = executor._submit_order
            executor._submit_order = lambda *a, **k: calls.append(a)
            try:
                r = executor.place_no_order(
                    "TID", 0.96, 5.0, ref="a", day="d",
                    cfg=_cfg(tmp, dry_run=True))
            finally:
                executor._submit_order = original
            self.assertEqual(r["status"], "dry_run")
            self.assertEqual(calls, [])
            ledger = json.loads((Path(tmp) / "ledger.json").read_text("utf-8"))
            self.assertIn("a", ledger["orders"])

    def test_dedup_same_ref(self):
        with TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, dry_run=True)
            executor.place_no_order("TID", 0.96, 5.0, ref="a", day="d", cfg=cfg)
            again = executor.place_no_order(
                "TID", 0.96, 5.0, ref="a", day="d", cfg=cfg)
            self.assertEqual(again["status"], "rejected")

    def test_places_and_caps_exposure(self):
        with TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, max_exposure=12.0)
            original = executor._submit_order
            executor._submit_order = lambda client, tid, price, shares: {
                "orderID": "X", "success": True}
            try:
                first = executor.place_no_order(
                    "TID", 0.96, 8.0, ref="a", day="d", cfg=cfg, client=object())
                second = executor.place_no_order(
                    "TID", 0.96, 8.0, ref="b", day="d", cfg=cfg, client=object())
            finally:
                executor._submit_order = original
            self.assertEqual(first["status"], "placed")
            self.assertEqual(second["status"], "rejected")  # 8+8 passa de 12


if __name__ == "__main__":
    unittest.main()
