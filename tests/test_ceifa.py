import unittest
import datetime as dt
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tmax import ceifa


class WarmTargetRiskTests(unittest.TestCase):
    def test_blocks_london_case(self):
        self.assertTrue(ceifa.is_warm_target_risk(
            "EGLC", "27°C", 1.2, 26.2, 27.5))

    def test_allows_small_nowcast_shift(self):
        self.assertFalse(ceifa.is_warm_target_risk(
            "EGLC", "27°C", 0.9, 26.2, 27.5))

    def test_blocks_when_raw_deviation_reaches_one_degree(self):
        self.assertTrue(ceifa.is_warm_target_risk(
            "EGLC", "27°C", 0.8, 26.2, 27.5,
            observed_deviation=1.1))

    def test_requires_plausible_target_even_with_hot_raw_deviation(self):
        self.assertFalse(ceifa.is_warm_target_risk(
            "EGLC", "30°C", 0.8, 26.2, 27.5,
            observed_deviation=1.4))

    def test_plateau_extends_lower_bound_to_observed_maximum(self):
        self.assertTrue(ceifa.is_warm_target_risk(
            "CYYZ", "26°C", 1.05, 27.5, 29.3,
            observed_deviation=1.5, plateau_temp=26.0))

    def test_same_target_without_plateau_remains_outside_range(self):
        self.assertFalse(ceifa.is_warm_target_risk(
            "CYYZ", "26°C", 1.05, 27.5, 29.3,
            observed_deviation=1.5))

    def test_detects_two_hour_plateau_at_daily_maximum(self):
        base = dt.datetime(2026, 7, 26, 12, tzinfo=dt.timezone.utc)
        obs = [{"time": base + dt.timedelta(hours=i), "temp": 26.0}
               for i in range(3)]
        self.assertEqual(ceifa.plateau_temperature(obs), 26.0)

    def test_does_not_call_cooling_after_peak_a_plateau_at_maximum(self):
        base = dt.datetime(2026, 7, 26, 12, tzinfo=dt.timezone.utc)
        obs = [
            {"time": base, "temp": 27.0},
            {"time": base + dt.timedelta(hours=1), "temp": 26.0},
            {"time": base + dt.timedelta(hours=3), "temp": 26.0},
        ]
        self.assertIsNone(ceifa.plateau_temperature(obs))

    def test_reconstructs_plateau_for_legacy_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            station = root / "CYYZ"
            station.mkdir()
            with gzip.open(station / "2026-07-26.json.gz", "wt",
                           encoding="utf-8") as handle:
                json.dump({"obs": [
                    ["2026-07-26 12:00", 26.0],
                    ["2026-07-26 13:00", 26.0],
                    ["2026-07-26 14:00", 26.0],
                ]}, handle)
            old_root = ceifa.BACKTEST_ARCHIVE
            try:
                ceifa.BACKTEST_ARCHIVE = root
                ceifa._archived_observations.cache_clear()
                value = ceifa.reconstructed_plateau_temperature(
                    "CYYZ", "2026-07-26", "2026-07-26T18:15:00Z")
            finally:
                ceifa.BACKTEST_ARCHIVE = old_root
                ceifa._archived_observations.cache_clear()
            self.assertEqual(value, 26.0)

    def test_reconstructs_conservative_raw_deviation_for_old_snapshot(self):
        deviation = ceifa.reconstructed_observed_deviation(
            "EGLC", "2026-07-26T13:00:00Z", 0.76)
        self.assertAlmostEqual(deviation, 0.76 / 0.7)

    def test_allows_target_outside_plausible_range(self):
        self.assertFalse(ceifa.is_warm_target_risk(
            "EGLC", "30°C", 1.2, 26.2, 27.5))

    def test_converts_fahrenheit_contract_to_celsius(self):
        self.assertTrue(ceifa.is_warm_target_risk(
            "KLGA", "81°F", 1.1, 26.0, 27.0))

    def test_minimum_archive_without_ensemble_columns_still_runs(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:10:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "17°C",
                 "preco_sim": 0.02, "preco_nao": 0.98},
                {"ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "17°C",
                 "preco_sim": 0.01, "preco_nao": 0.99},
            ]).to_parquet(archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "pico_hora": 15},
            ]).to_parquet(archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False)

            self.assertEqual(result["n"], 1)
            self.assertEqual(result["wins"], 1)

    def test_checked_book_without_ask_does_not_create_backtest_entry(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:10:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.03, "preco_nao": 0.97,
                 "ask_nao": None, "livro_consultado": True},
                {"ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.01, "preco_nao": 0.99,
                 "ask_nao": None, "livro_consultado": True},
            ]).to_parquet(archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "pico_hora": 15},
            ]).to_parquet(archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False)

            self.assertEqual(result["n"], 0)

    def test_repeated_strategy_uses_one_percent_of_free_cash(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            market_rows = [
                {"ts_utc": f"2026-07-26T13:{minute:02d}:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.02, "preco_nao": 0.98}
                for minute in (0, 5, 10)
            ]
            market_rows.append({
                "ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "faixa": "30°C",
                "preco_sim": 0.01, "preco_nao": 0.99})
            pd.DataFrame(market_rows).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-07-26T12:59:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "pico_hora": 15,
            }]).to_parquet(archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_repeated(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False,
                interval_minutes=5, stake_frac=0.01)

            self.assertEqual(result["n"], 3)
            self.assertEqual(result["wins"], 3)
            stakes = [s["stake"] for s in result["signals"]]
            self.assertAlmostEqual(stakes[0], 0.01)
            self.assertAlmostEqual(stakes[1], 0.0099)
            self.assertAlmostEqual(stakes[2], 0.009801)
            self.assertAlmostEqual(
                result["real_mult"],
                0.99 ** 3 + sum(stake / 0.98 for stake in stakes))


if __name__ == "__main__":
    unittest.main()
