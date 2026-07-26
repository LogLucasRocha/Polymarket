import datetime as dt
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tmax import capture, config, distribution


class NowcastDiagnosticsTests(unittest.TestCase):
    def test_returns_hourly_components_used_in_average(self):
        times = [dt.datetime(2026, 7, 26, h, tzinfo=dt.timezone.utc)
                 for h in (10, 11, 12)]
        members = {
            ("ecmwf_ifs025", "01"): [20.0, 21.0, 22.0],
            ("ecmwf_ifs025", "02"): [22.0, 23.0, 24.0],
        }
        bias = {"ecmwf": {"bias": 1.0}}
        obs = [
            {"time": times[0], "temp": 21.0, "raw": "METAR A"},
            {"time": times[1], "temp": 23.0, "raw": "METAR B"},
            {"time": times[2], "temp": 25.0, "raw": "METAR C"},
        ]

        result = distribution.compute_nowcast_offset(
            obs, times, members, bias)

        self.assertEqual(result["n_hours"], 3)
        self.assertAlmostEqual(result["offset"], 2.0)
        self.assertEqual([c["deviation_c"] for c in result["components"]],
                         [1.0, 2.0, 3.0])
        self.assertEqual(result["components"][1]["member_count"], 2)
        self.assertEqual(result["components"][2]["raw_metar"], "METAR C")

    def test_archives_one_row_per_component(self):
        now = dt.datetime(2026, 7, 26, 13, tzinfo=dt.timezone.utc)
        component_time = dt.datetime(2026, 7, 26, 12, 50,
                                     tzinfo=dt.timezone.utc)
        nowcast = {
            "offset": 1.7, "shift": 1.19, "hour_weight": 1.0,
            "damping": 0.7, "n_hours": 1,
            "components": [{
                "observation_time": component_time,
                "ensemble_hour": component_time.replace(minute=0),
                "observed_temp_c": 26.0, "ensemble_mean_c": 24.3,
                "deviation_c": 1.7, "member_count": 82,
                "raw_metar": "METAR EGLC ...",
            }],
        }
        with TemporaryDirectory() as tmp:
            buffer_dir = Path(tmp)
            with mock.patch.object(capture, "BUFFER_DIR", buffer_dir):
                capture.record_nowcast(
                    now, "EGLC", now.date(), nowcast)
            path = buffer_dir / "nowcast" / "2026-07-26.jsonl"
            row = json.loads(path.read_text("utf-8").strip())

        self.assertEqual(row["icao"], "EGLC")
        self.assertEqual(row["member_count"], 82)
        self.assertEqual(row["deviation_c"], 1.7)
        self.assertEqual(row["nowcast_offset"], 1.7)

    def test_flushes_nowcast_partition_by_station(self):
        now = dt.datetime(2026, 7, 26, 13, tzinfo=dt.timezone.utc)
        nowcast = {
            "offset": 1.0, "shift": 0.7, "hour_weight": 1.0,
            "damping": 0.7, "n_hours": 1,
            "components": [{
                "observation_time": now, "ensemble_hour": now,
                "observed_temp_c": 25.0, "ensemble_mean_c": 24.0,
                "deviation_c": 1.0, "member_count": 82,
                "raw_metar": "METAR EGLC ...",
            }],
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (mock.patch.object(capture, "BUFFER_DIR", root / "buffer"),
                  mock.patch.object(capture, "FILES_DIR", root / "files"),
                  mock.patch.object(capture, "ARCHIVE_DIR", root / "archive")):
                capture.record_nowcast(now, "EGLC", now.date(), nowcast)
                changed = capture.flush(now + dt.timedelta(days=1))
            expected = (root / "archive" / "nowcast" / "EGLC" /
                        "2026-07-26.parquet")

            self.assertIn(expected, changed)
            self.assertTrue(expected.exists())


if __name__ == "__main__":
    unittest.main()
