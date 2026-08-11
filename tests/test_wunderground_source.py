import datetime as dt
import unittest
from unittest.mock import patch

from tmax import config, fetch


class _Response:
    def __init__(self, *, text="", payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class WundergroundResolutionSourceTests(unittest.TestCase):
    def setUp(self):
        fetch._wunderground_api_key.cache_clear()
        self.station = config.STATIONS["ZGSZ"]

    def tearDown(self):
        fetch._wunderground_api_key.cache_clear()

    def test_shenzhen_uses_lau_fau_shan_coordinates_and_source(self):
        self.assertAlmostEqual(self.station.lat, 22.4688889)
        self.assertAlmostEqual(self.station.lon, 113.9836111)
        self.assertEqual(self.station.wu_location_id, "ZGSZ:9:CN")
        self.assertEqual(self.station.wu_observation_id, "45035")

    def test_reads_and_validates_the_observation_that_resolves_the_market(self):
        epoch = int(dt.datetime(
            2026, 8, 7, 8, tzinfo=dt.timezone.utc).timestamp())
        responses = [
            _Response(text='{"API_KEY":"public-key"}'),
            _Response(payload={"observations": [{
                "valid_time_gmt": epoch,
                "temp": 35,
                "dewPt": 27,
                "wdir": 200,
                "wspd": 7,
                "obs_id": "45035",
                "obs_name": "Lau Fau Shan",
            }]}),
        ]
        with patch("tmax.fetch._get", side_effect=responses) as mocked:
            observations = fetch.fetch_wunderground_observations(
                self.station, dt.date(2026, 8, 7), dt.date(2026, 8, 7))

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["temp"], 35.0)
        self.assertEqual(observations[0]["time"].hour, 16)
        self.assertEqual(observations[0]["source_id"], "45035")
        self.assertEqual(observations[0]["source_name"], "Lau Fau Shan")
        self.assertEqual(mocked.call_count, 2)

    def test_rejects_a_silent_station_change(self):
        responses = [
            _Response(text='{"API_KEY":"public-key"}'),
            _Response(payload={"observations": [{
                "valid_time_gmt": 1786089600,
                "temp": 35,
                "obs_id": "59493",
                "obs_name": "Shenzhen",
            }]}),
        ]
        with patch("tmax.fetch._get", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "Fonte Wunderground"):
                fetch.fetch_wunderground_observations(
                    self.station, dt.date(2026, 8, 7),
                    dt.date(2026, 8, 7))


if __name__ == "__main__":
    unittest.main()
