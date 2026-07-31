import datetime as dt
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from tmax import monitor


class MonitorTest(unittest.TestCase):
    def test_dashboard_refresh_updates_only_archive_paths(self):
        replies = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with mock.patch.object(monitor.subprocess, "run",
                               side_effect=replies) as invoked:
            result = monitor.sync_dashboard_data()

        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        commands = [call.args[0][1:] for call in invoked.call_args_list]
        self.assertIn(["fetch", "origin", "--quiet"], commands)
        self.assertIn(
            ["restore", "--source=origin/main", "--worktree", "--",
             "dados", "dados_low"],
            commands)
        self.assertFalse(any(command[0] == "merge" for command in commands))

    def test_dashboard_refresh_keeps_local_code_when_data_is_current(self):
        replies = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with mock.patch.object(monitor.subprocess, "run",
                               side_effect=replies) as invoked:
            result = monitor.sync_dashboard_data()

        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])
        commands = [call.args[0][1:] for call in invoked.call_args_list]
        self.assertFalse(any(command[0] in ("merge", "restore")
                             for command in commands))

    def test_dashboard_refresh_reports_archive_restore_failure(self):
        replies = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr="failure"),
        ]
        with mock.patch.object(monitor.subprocess, "run", side_effect=replies):
            result = monitor.sync_dashboard_data()

        self.assertFalse(result["ok"])
        self.assertIn("arquivos de dados", result["message"])

    def test_risk_metrics_reconstructs_single_entry_stakes(self):
        signals = [
            {"icao": "EGLC", "day": "2026-07-24", "faixa": "16°C",
             "ts": dt.datetime(2026, 7, 24, 1), "price": 0.98, "won": True},
            {"icao": "RKSI", "day": "2026-07-24", "faixa": "27°C",
             "ts": dt.datetime(2026, 7, 24, 2), "price": 0.98, "won": False},
        ]

        metrics = monitor.risk_metrics({"signals": signals, "per_day": []})

        self.assertGreater(metrics["gross_wins"], 0)
        self.assertGreater(metrics["gross_losses"], 0)

    def test_slice_preserves_minimum_repeated_sizing(self):
        signals = [
            {"icao": "EGLC", "day": "2026-07-24", "faixa": "16°C",
             "ts": dt.datetime(2026, 7, 24, 1, minute), "price": 0.98,
             "won": True, "stopped": False, "loss_frac": None}
            for minute in (0, 5)
        ]
        stats = {"signals": signals, "repeat_minutes": 5,
                 "archive_kind": "minimum"}

        sliced = monitor.slice_strategy(stats, 7)

        self.assertEqual(sliced["repeat_minutes"], 5)
        self.assertEqual(sliced["archive_kind"], "minimum")
        stakes = [signal["stake"] for signal in sliced["signals"]]
        self.assertAlmostEqual(stakes[0], 0.01)
        self.assertAlmostEqual(stakes[1], 0.0099)

    def test_loss_date_filter_orders_days_and_selects_only_requested_day(self):
        losses = pd.DataFrame([
            {"Dia": "2026-07-23", "Chave": "WMKK · 2026-07-23 · 32°C"},
            {"Dia": "2026-07-29", "Chave": "ZHHH · 2026-07-29 · 33°C"},
            {"Dia": "2026-07-29", "Chave": "EGLC · 2026-07-29 · 36°C"},
        ])

        self.assertEqual(
            monitor.loss_days(losses), ["2026-07-29", "2026-07-23"])
        selected = monitor.losses_on_day(losses, "2026-07-29")
        self.assertEqual(
            selected["Chave"].tolist(),
            ["ZHHH · 2026-07-29 · 33°C", "EGLC · 2026-07-29 · 36°C"],
        )


if __name__ == "__main__":
    unittest.main()
