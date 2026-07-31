import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from lowtemp import capture
from tmax import distribution


class MinimumCaptureTest(unittest.TestCase):
    def _context(self):
        timezone = dt.timezone.utc
        times = [dt.datetime(2026, 7, 27, hour, tzinfo=timezone)
                 for hour in (0, 1, 2, 3)]
        members = {
            ("ecmwf_ifs025", "01"): [14.0, 12.0, 13.0, 15.0],
            ("ecmwf_ifs025", "02"): [15.0, 13.0, 14.0, 16.0],
        }
        component = {
            "observation_time": times[0], "ensemble_hour": times[0],
            "observed_temp_c": 13.0, "ensemble_mean_c": 12.0,
            "deviation_c": 1.0, "member_count": 2,
            "raw_metar": "METAR TEST",
        }
        return {
            "now": dt.datetime(2026, 7, 27, 0, 30, tzinfo=timezone),
            "d0": dt.date(2026, 7, 27),
            "ens": {"time": times, "members": members},
            "bias": {"ecmwf": {"bias": 1.0}},
            "shift": 0.5,
            "obs_today": [{"time": times[0], "temp": 13.0,
                           "raw": "METAR TEST"}],
            "latest_metar": {"time": times[0], "temp": 13.0,
                             "raw": "METAR TEST"},
            "nowcast": {"offset": 1.0, "shift": 0.5,
                        "hour_weight": 1.0, "damping": 0.5,
                        "n_hours": 1, "components": [component]},
        }

    def test_forecast_snapshot_contains_minimum_distribution_and_nowcast(self):
        context = self._context()
        now = context["now"]

        row = capture._forecast_snapshot(context, "EGLC", now.date(), now)

        self.assertEqual(row["obs_min"], 13.0)
        self.assertEqual(row["nowcast_offset"], 1.0)
        self.assertEqual(row["nowcast_n_hours"], 1)
        self.assertEqual(row["n_membros"], 2)
        self.assertLessEqual(row["p10"], row["mediana"])
        self.assertLessEqual(row["mediana"], row["p90"])
        self.assertAlmostEqual(
            row["spread_frio"], row["mediana"] - row["piso_ens"])
        self.assertAlmostEqual(
            row["spread_ens"], row["teto_ens"] - row["piso_ens"])
        self.assertIn("mediana_bruta", row)
        self.assertEqual(row["bias_source"], "tmax_station_lookback")
        self.assertEqual(row["schema_version"], 2)

    def test_builds_complete_nowcast_and_metar_rows(self):
        context = self._context()
        now = context["now"]

        nowcast = capture._nowcast_rows(context, "EGLC", now.date(), now)
        metar = capture._metar_rows(context, "EGLC", now.date(), now)

        self.assertEqual(nowcast[0]["deviation_c"], 1.0)
        self.assertEqual(nowcast[0]["raw_metar"], "METAR TEST")
        self.assertEqual(metar[0]["temp_c"], 13.0)
        self.assertEqual(metar[0]["raw_metar"], "METAR TEST")

    def test_saves_all_four_archives(self):
        captured = dt.datetime(2026, 7, 26, 12, tzinfo=dt.timezone.utc)
        market = [{"ts_utc": captured.isoformat(), "icao": "EGLC",
                   "dia": "2026-07-26", "faixa": "13°C",
                   "preco_sim": 0.02, "preco_nao": 0.98}]
        forecast = [{"ts_utc": captured.isoformat(), "icao": "EGLC",
                     "dia": "2026-07-26", "pico_hora": 5,
                     "mediana": 13.0, "p10": 12.0, "p90": 14.0}]
        nowcast = [{"ts_utc": captured.isoformat(), "icao": "EGLC",
                    "dia": "2026-07-26", "observation_time": captured,
                    "deviation_c": 1.0}]
        metar = [{"ts_utc": captured.isoformat(), "icao": "EGLC",
                  "dia": "2026-07-26", "observation_time": captured,
                  "temp_c": 13.0, "raw_metar": "METAR TEST"}]

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (mock.patch.object(capture, "BUF_DIR", root / "buffer"),
                  mock.patch.object(capture, "ARCH_DIR", root / "archive")):
                capture.salvar(market, forecast, nowcast, metar)
            for base in ("mercado", "previsao", "nowcast", "metar"):
                path = root / "archive" / base / "2026-07-26.parquet"
                self.assertTrue(path.exists(), base)
                self.assertFalse(pd.read_parquet(path).empty)

    def test_rich_forecast_merges_with_legacy_daily_archive(self):
        first = dt.datetime(2026, 7, 26, 12, tzinfo=dt.timezone.utc)
        second = first + dt.timedelta(minutes=5)
        legacy = [{"ts_utc": first.isoformat(), "icao": "EGLC",
                   "dia": "2026-07-26", "pico_hora": 5}]
        rich = [{"ts_utc": second.isoformat(), "icao": "EGLC",
                 "dia": "2026-07-26", "pico_hora": 5,
                 "mediana": 13.0, "p10": 12.0, "p90": 14.0,
                 "spread_frio": 2.0, "nowcast_shift": 0.4}]

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (mock.patch.object(capture, "BUF_DIR", root / "buffer"),
                  mock.patch.object(capture, "ARCH_DIR", root / "archive")):
                capture.salvar([], legacy)
                capture.salvar([], rich)

            frame = pd.read_parquet(
                root / "archive" / "previsao" / "2026-07-26.parquet")
            self.assertEqual(len(frame), 2)
            self.assertIn("nowcast_shift", frame.columns)
            self.assertEqual(frame.iloc[-1]["nowcast_shift"], 0.4)

    def test_collection_archives_executable_yes_offer(self):
        station = SimpleNamespace(tz=dt.timezone.utc)
        event = {"rows": [{"label": "13°C"}]}
        odds = [{
            "label": "13°C", "yes": 0.97, "no": 0.03,
            "yes_ask": 0.975, "yes_ask_size": 12.0,
            "no_ask": 0.04, "no_ask_size": 8.0,
            "book_checked": True,
        }]
        with (mock.patch.object(capture.config, "STATIONS", {"EGLC": station}),
              mock.patch.object(capture.config, "STATIONS_FAHRENHEIT", {}),
              mock.patch.object(capture.pm, "fetch_event", return_value=event),
              mock.patch.object(capture.pm, "attach_best_asks") as attach,
              mock.patch.object(capture.pm, "odds_rows", return_value=odds),
              mock.patch.object(capture.pipeline, "build_context",
                                return_value=self._context()),
              mock.patch.object(capture, "_forecast_snapshot",
                                return_value=None),
              mock.patch.object(capture, "_nowcast_rows", return_value=[]),
              mock.patch.object(capture, "_metar_rows", return_value=[])):
            market, _forecast, _nowcast, _metar = capture.coletar()

        attach.assert_called_once_with(event)
        self.assertEqual(market[0]["ask_sim"], 0.975)
        self.assertEqual(market[0]["ask_sim_volume"], 12.0)
        self.assertTrue(market[0]["livro_consultado"])


class MinimumDistributionTest(unittest.TestCase):
    def test_member_minima_combines_observed_floor_and_future(self):
        times = [dt.datetime(2026, 7, 27, hour, tzinfo=dt.timezone.utc)
                 for hour in (0, 1, 2)]
        members = {("ecmwf_ifs025", "01"): [10.0, 12.0, 14.0]}

        result = distribution.member_minima_for_day(
            times[0].date(), times, members,
            bias={"ecmwf": {"bias": 1.0}},
            now=times[0], shift=0.5, obs_min=11.0)

        self.assertEqual(result[0]["tmin"], 11.0)
        self.assertEqual(result[0]["future_min"], 11.5)


if __name__ == "__main__":
    unittest.main()
