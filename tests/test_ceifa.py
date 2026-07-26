import unittest
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


if __name__ == "__main__":
    unittest.main()
