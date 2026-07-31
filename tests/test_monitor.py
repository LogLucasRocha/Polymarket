import datetime as dt
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from zipfile import ZipFile

import pandas as pd

from tmax import monitor


class MonitorTest(unittest.TestCase):
    def test_city_frame_is_empty_without_raising_when_strategy_has_no_entries(self):
        frame = monitor.city_frame({"by_city": {}})

        self.assertTrue(frame.empty)
        self.assertEqual(
            frame.columns.tolist(),
            ["Cidade", "ICAO", "Parcelas", "Acertos", "Erros",
             "Assertividade"],
        )

    def test_dashboard_refresh_updates_only_archive_paths(self):
        replies = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with (mock.patch.object(monitor.subprocess, "run",
                                side_effect=replies) as invoked,
              mock.patch.object(monitor, "_sync_live_snapshot",
                                return_value={"ok": True, "updated": False,
                                              "message": "live"})):
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
        with (mock.patch.object(monitor.subprocess, "run",
                                side_effect=replies) as invoked,
              mock.patch.object(monitor, "_sync_live_snapshot",
                                return_value={"ok": True, "updated": False,
                                              "message": "live"})):
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
        with (mock.patch.object(monitor.subprocess, "run", side_effect=replies),
              mock.patch.object(monitor, "_sync_live_snapshot",
                                return_value={"ok": False, "updated": False,
                                              "message": "sem live"})):
            result = monitor.sync_dashboard_data()

        self.assertFalse(result["ok"])
        self.assertIn("arquivos de dados", result["message"])

    def test_materializes_maximum_and_minimum_intraday_rows(self):
        rows = {
            ("maximum", "mercado"): [{
                "ts_utc": "2026-07-31T12:00:00Z", "icao": "EGLC",
                "dia": "2026-07-31", "faixa": "30°C",
                "preco_sim": 0.02, "preco_nao": 0.98,
            }],
            ("minimum", "previsao"): [{
                "ts_utc": "2026-07-31T12:00:00Z", "icao": "EGLC",
                "dia": "2026-07-31", "pico_hora": 5,
            }],
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (mock.patch.object(monitor.config, "DATA_DIR", root / "data"),
                  mock.patch.object(monitor, "MAXIMUM_LIVE_ARCHIVE",
                                    root / "dados_live"),
                  mock.patch.object(monitor, "MINIMUM_LIVE_ARCHIVE",
                                    root / "dados_low_live")):
                written = monitor._write_live_frames(rows)

            maximum = pd.read_parquet(
                root / "dados_live" / "mercado" / "EGLC" /
                "2026-07-31.parquet")
            minimum = pd.read_parquet(
                root / "dados_low_live" / "previsao" / "EGLC" /
                "2026-07-31.parquet")
            self.assertEqual(written, 2)
            self.assertTrue(maximum.iloc[0]["snapshot_live"])
            self.assertTrue(minimum.iloc[0]["snapshot_live"])

    def test_downloads_release_asset_and_builds_live_archives(self):
        payload = io.BytesIO()
        maximum = {
            "ts_utc": "2026-07-31T12:00:00Z", "icao": "EGLC",
            "dia": "2026-07-31", "faixa": "30°C",
            "preco_sim": 0.02, "preco_nao": 0.98,
        }
        minimum = {
            "ts_utc": "2026-07-31T12:00:00Z", "icao": "EGLC",
            "dia": "2026-07-31", "pico_hora": 5,
        }
        with ZipFile(payload, "w") as archive:
            archive.writestr(
                "data/capture/buffer/mercado/2026-07-31.jsonl",
                json.dumps(maximum) + "\n")
            archive.writestr(
                "data_low/previsao.jsonl", json.dumps(minimum) + "\n")
        release = mock.Mock()
        release.json.return_value = {"assets": [{
            "name": "ceifa-live.zip", "id": 10,
            "updated_at": "2026-07-31T12:01:00Z",
            "browser_download_url": "https://example.test/ceifa-live.zip",
        }]}
        download = mock.Mock(content=payload.getvalue())

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (mock.patch.object(monitor.requests, "get",
                                    side_effect=[release, download]),
                  mock.patch.object(monitor.config, "DATA_DIR", root / "data"),
                  mock.patch.object(monitor, "MAXIMUM_LIVE_ARCHIVE",
                                    root / "dados_live"),
                  mock.patch.object(monitor, "MINIMUM_LIVE_ARCHIVE",
                                    root / "dados_low_live")):
                result = monitor._sync_live_snapshot()

            self.assertTrue(result["ok"])
            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["captured"], "2026-07-31T12:00:00Z")
            self.assertTrue((root / "dados_live" / "mercado" / "EGLC" /
                             "2026-07-31.parquet").exists())
            self.assertTrue((root / "dados_low_live" / "previsao" / "EGLC" /
                             "2026-07-31.parquet").exists())

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
