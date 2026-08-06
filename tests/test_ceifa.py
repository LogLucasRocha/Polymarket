import unittest
import datetime as dt
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tmax import ceifa


class WarmTargetRiskTests(unittest.TestCase):
    def test_intraday_price_is_not_treated_as_resolved_before_zero_or_one(self):
        open_group = pd.DataFrame([{
            "preco_nao": 0.98, "snapshot_live": True,
        }])
        resolved_group = pd.DataFrame([{
            "preco_nao": 1.0, "snapshot_live": True,
        }])

        self.assertIsNone(ceifa._resolved_price(open_group, "preco_nao"))
        self.assertEqual(
            ceifa._resolved_price(resolved_group, "preco_nao"), 1.0)

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

    def test_blocks_upper_tail_close_to_ensemble_ceiling(self):
        self.assertTrue(ceifa.is_upper_tail_ceiling_risk(
            "ZUUU", "34°C or higher", 33.33))

    def test_allows_upper_tail_with_more_than_safety_margin(self):
        self.assertFalse(ceifa.is_upper_tail_ceiling_risk(
            "ZUUU", "35°C or higher", 33.33))

    def test_upper_tail_filter_does_not_apply_to_exact_band(self):
        self.assertFalse(ceifa.is_upper_tail_ceiling_risk(
            "ZUUU", "34°C", 34.0))

    def test_blocks_exact_band_when_p90_touches_lower_boundary(self):
        self.assertTrue(ceifa.is_ensemble_inside_market_band_risk(
            "RPLL", "32°C", 29.0, 31.5, enabled=True))

    def test_blocks_exact_band_inside_p10_p90(self):
        self.assertTrue(ceifa.is_ensemble_inside_market_band_risk(
            "RPLL", "32°C", 31.4, 32.2, enabled=True))

    def test_blocks_tel_aviv_when_p90_touches_upper_boundary(self):
        self.assertTrue(ceifa.is_ensemble_inside_market_band_risk(
            "LLBG", "37°C", 36.0, 37.5, enabled=True))

    def test_allows_exact_band_above_p10_p90(self):
        self.assertFalse(ceifa.is_ensemble_inside_market_band_risk(
            "RPLL", "33°C", 29.0, 31.8, enabled=True))

    def test_fahrenheit_range_uses_half_degree_resolution_boundaries(self):
        inside_c = (89.5 - 32.0) * 5.0 / 9.0
        outside_c = (89.4 - 32.0) * 5.0 / 9.0
        self.assertTrue(ceifa.is_ensemble_inside_market_band_risk(
            "KORD", "90-91°F", inside_c, inside_c + 1.0, enabled=True))
        self.assertFalse(ceifa.is_ensemble_inside_market_band_risk(
            "KORD", "90-91°F", outside_c - 1.0, outside_c,
            enabled=True))

    def test_ensemble_band_rule_is_inactive_operationally(self):
        self.assertFalse(ceifa.is_ensemble_inside_market_band_risk(
            "RPLL", "32°C", 29.0, 31.5))

    def test_minimum_archive_with_rich_forecast_keeps_maximum_filter_off(self):
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
                 "dia": "2026-07-26", "pico_hora": 15,
                 "mediana": 17.0, "teto_ens": 21.0,
                 "piso_ens": 13.0, "spread_frio": 4.0},
            ]).to_parquet(archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False,
                uncertainty_filter=False)

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

    def test_available_share_count_does_not_filter_an_ask_price(self):
        market = pd.DataFrame([{
            "preco_nao": 0.96, "ask_nao": 0.97,
            "ask_nao_volume": 0.01, "livro_consultado": True,
        }])

        candidates = ceifa._execution_candidates(market, "NAO")

        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates.iloc[0]["_entry_price"], 0.97)

    def test_minimum_taf_filter_replays_archived_block(self):
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
            pd.DataFrame([{
                "ts_utc": "2026-07-26T13:00:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "pico_hora": 15,
                "taf_convective_blocked": True,
                "taf_convective_codes": "VCTS",
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_repeated(
                icaos={"EGLC"}, archive=archive,
                warm_target_filter=False, uncertainty_filter=False,
                minimum_taf_filter=True)

            self.assertEqual(result["n"], 0)
            self.assertEqual(result["n_filtrado_taf"], 1)
            self.assertEqual(result["n_filtrado_100c"], 1)
            performance = result["filter_performance"]["taf"]
            self.assertEqual(performance["to_100c"], 1)
            self.assertEqual(performance["to_0c"], 0)
            self.assertGreater(performance["return"], 0)

    def test_repeated_strategy_filters_upper_tail_near_ceiling(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-07-29T07:11:00Z", "icao": "ZUUU",
                 "dia": "2026-07-29", "faixa": "34°C or higher",
                 "preco_sim": 0.04, "preco_nao": 0.96},
                {"ts_utc": "2026-07-29T12:00:00Z", "icao": "ZUUU",
                 "dia": "2026-07-29", "faixa": "34°C or higher",
                 "preco_sim": 0.99, "preco_nao": 0.01},
            ]).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-07-29T07:10:00Z", "icao": "ZUUU",
                "dia": "2026-07-29", "pico_hora": 16,
                "mediana": 32.2, "p90": 32.8, "teto_ens": 33.33,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_repeated(
                icaos={"ZUUU"}, archive=archive,
                uncertainty_filter=False)

            self.assertEqual(result["n"], 0)
            self.assertEqual(result["n_filtrado_upper_tail"], 1)
            self.assertEqual(result["n_filtrado_0c"], 1)

    def test_repeated_strategy_filters_band_containing_p90(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-08-01T02:55:00Z", "icao": "RPLL",
                 "dia": "2026-08-01", "faixa": "32°C",
                 "preco_sim": 0.04, "preco_nao": 0.96},
                {"ts_utc": "2026-08-01T12:00:00Z", "icao": "RPLL",
                 "dia": "2026-08-01", "faixa": "32°C",
                 "preco_sim": 0.99, "preco_nao": 0.01},
            ]).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-08-01T02:50:00Z", "icao": "RPLL",
                "dia": "2026-08-01", "pico_hora": 11,
                "mediana": 30.0, "p10": 29.0, "p90": 31.5,
                "teto_ens": 31.8,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_repeated(
                icaos={"RPLL"}, archive=archive,
                uncertainty_filter=False, ensemble_band_filter=True)

            self.assertEqual(result["n"], 0)
            self.assertEqual(result["n_filtrado_ensemble_band"], 1)
            self.assertEqual(result["n_filtrado_0c"], 1)

    def test_repeated_minimum_band_filter_is_optional(self):
        """Mínimas: o veto P10–P90 só age quando ligado explicitamente.

        Reproduz o caso do Paris (LFPB): faixa 21°C com P90 21,0 °C encosta
        no intervalo P10–P90 e deve ser vetada quando ``ensemble_band_filter``
        está ligado, sem herdar os vetos de cauda quente das máximas.
        """
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-08-01T03:05:00Z", "icao": "LFPB",
                 "dia": "2026-08-01", "faixa": "21°C",
                 "preco_sim": 0.01, "preco_nao": 0.99},
                {"ts_utc": "2026-08-01T12:00:00Z", "icao": "LFPB",
                 "dia": "2026-08-01", "faixa": "21°C",
                 "preco_sim": 0.01, "preco_nao": 0.99},
            ]).to_parquet(archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-08-01T03:00:00Z", "icao": "LFPB",
                "dia": "2026-08-01", "pico_hora": 6,
                "mediana": 21.0, "p10": 19.7, "p90": 21.0,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            base = ceifa.simulate_repeated(
                icaos={"LFPB"}, archive=archive,
                warm_target_filter=False, uncertainty_filter=False)
            self.assertEqual(base["n"], 1)
            self.assertEqual(base["n_filtrado_ensemble_band"], 1)
            self.assertEqual(
                base["filter_performance"]["ensemble_band"]["to_100c"], 1)

            filtered = ceifa.simulate_repeated(
                icaos={"LFPB"}, archive=archive,
                warm_target_filter=False, uncertainty_filter=False,
                ensemble_band_filter=True)
            self.assertEqual(filtered["n"], 0)
            self.assertEqual(filtered["n_filtrado_ensemble_band"], 1)

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
                "mediana": 16.0, "teto_ens": 20.0,
            }]).to_parquet(archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_repeated(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False,
                uncertainty_filter=False, interval_minutes=5,
                stake_frac=0.01)

            self.assertEqual(result["n"], 3)
            self.assertEqual(result["wins"], 3)
            stakes = [s["stake"] for s in result["signals"]]
            self.assertAlmostEqual(stakes[0], 0.01)
            self.assertAlmostEqual(stakes[1], 0.0099)
            self.assertAlmostEqual(stakes[2], 0.009801)
            self.assertAlmostEqual(
                result["real_mult"],
                0.99 ** 3 + sum(stake / 0.98 for stake in stakes))

    def test_repeated_strategy_locks_most_expensive_band_on_first_round(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            rows = []
            for minute, prices in ((0, {"30°C": 0.970, "31°C": 0.985}),
                                   (5, {"30°C": 0.990, "31°C": 0.975})):
                for faixa, price in prices.items():
                    rows.append({
                        "ts_utc": f"2026-07-26T13:{minute:02d}:00Z",
                        "icao": "EGLC", "dia": "2026-07-26",
                        "faixa": faixa, "preco_sim": 1.0 - price,
                        "preco_nao": price,
                    })
            for faixa in ("30°C", "31°C"):
                rows.append({
                    "ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                    "dia": "2026-07-26", "faixa": faixa,
                    "preco_sim": 0.01, "preco_nao": 0.99,
                })
            pd.DataFrame(rows).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-07-26T12:59:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "pico_hora": 15,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            active = ceifa.simulate_repeated(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False,
                uncertainty_filter=False, interval_minutes=5)
            self.assertEqual(active["n"], 4)
            self.assertEqual(active["n_filtrado_faixa_unica"], 0)
            self.assertEqual(
                {signal["faixa"] for signal in active["signals"]},
                {"30°C", "31°C"})

            result = ceifa.simulate_repeated(
                icaos={"EGLC"}, archive=archive, warm_target_filter=False,
                uncertainty_filter=False, interval_minutes=5,
                single_band=True)

            self.assertEqual(result["n"], 2)
            self.assertEqual(result["n_filtrado_faixa_unica"], 2)
            self.assertEqual(
                {signal["faixa"] for signal in result["signals"]}, {"31°C"})
            self.assertEqual(
                [signal["price"] for signal in result["signals"]],
                [0.985, 0.975])

    def test_yes_strategy_uses_only_executable_yes_asks(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.96, "preco_nao": 0.04,
                 "ask_sim": 0.960, "livro_consultado": True},
                {"ts_utc": "2026-07-26T13:05:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.97, "preco_nao": 0.03,
                 "ask_sim": 0.970, "livro_consultado": True},
                {"ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.99, "preco_nao": 0.01,
                 "ask_sim": None, "livro_consultado": True},
            ]).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-07-26T12:59:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "pico_hora": 15,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_yes_repeated(
                icaos={"EGLC"}, archive=archive, interval_minutes=5,
                stake_frac=0.01)

            self.assertEqual(result["n"], 2)
            self.assertEqual(result["wins"], 2)
            self.assertEqual(result["side"], "SIM")
            self.assertTrue(all(signal["side"] == "SIM"
                                for signal in result["signals"]))

    def test_yes_strategy_does_not_use_legacy_indicative_price(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "mercado").mkdir()
            (archive / "previsao").mkdir()
            pd.DataFrame([
                {"ts_utc": "2026-07-26T13:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.97, "preco_nao": 0.03},
                {"ts_utc": "2026-07-26T18:00:00Z", "icao": "EGLC",
                 "dia": "2026-07-26", "faixa": "30°C",
                 "preco_sim": 0.99, "preco_nao": 0.01},
            ]).to_parquet(
                archive / "mercado" / "day.parquet", index=False)
            pd.DataFrame([{
                "ts_utc": "2026-07-26T12:59:00Z", "icao": "EGLC",
                "dia": "2026-07-26", "pico_hora": 15,
            }]).to_parquet(
                archive / "previsao" / "day.parquet", index=False)

            result = ceifa.simulate_yes_repeated(
                icaos={"EGLC"}, archive=archive)

            self.assertEqual(result["n"], 0)
            self.assertEqual(result["executable_snapshots"], 0)


if __name__ == "__main__":
    unittest.main()
