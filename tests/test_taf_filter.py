import datetime as dt
import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tmax import fetch


class MinimumTafFilterTests(unittest.TestCase):
    def setUp(self):
        self.station = SimpleNamespace(
            tz=ZoneInfo("America/New_York"), icao="KMIA")
        self.now = dt.datetime(
            2026, 8, 1, 5, 1, tzinfo=self.station.tz)

    @staticmethod
    def _epoch(value: str) -> int:
        return int(dt.datetime.fromisoformat(value).timestamp())

    def _forecast(self, start: str, end: str, weather: str) -> dict:
        return {
            "timeFrom": self._epoch(start),
            "timeTo": self._epoch(end),
            "wxString": weather,
            "fcstChange": "FM",
        }

    def test_blocks_future_vcts_in_remaining_local_day(self):
        forecasts = [self._forecast(
            "2026-08-01T18:00:00+00:00",
            "2026-08-02T01:00:00+00:00", "VCTS")]

        windows = fetch.minimum_convection_windows(
            forecasts, self.now, self.station)

        self.assertEqual(windows[0]["codes"], ["VCTS"])

    def test_blocks_intensified_tsra(self):
        forecasts = [self._forecast(
            "2026-08-01T15:00:00+00:00",
            "2026-08-01T18:00:00+00:00", "+TSRA BR")]

        windows = fetch.minimum_convection_windows(
            forecasts, self.now, self.station)

        self.assertEqual(windows[0]["codes"], ["TSRA"])

    def test_does_not_block_cb_or_tempo_without_selected_codes(self):
        forecast = self._forecast(
            "2026-08-01T15:00:00+00:00",
            "2026-08-01T18:00:00+00:00", "RA")
        forecast["clouds"] = [{"cover": "SCT", "type": "CB"}]
        forecast["fcstChange"] = "TEMPO"

        windows = fetch.minimum_convection_windows(
            [forecast], self.now, self.station)

        self.assertEqual(windows, [])

    def test_ignores_convection_after_local_day_ends(self):
        forecasts = [self._forecast(
            "2026-08-02T04:00:00+00:00",
            "2026-08-02T07:00:00+00:00", "TSRA")]

        windows = fetch.minimum_convection_windows(
            forecasts, self.now, self.station)

        self.assertEqual(windows, [])


if __name__ == "__main__":
    unittest.main()
