import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import matplotlib.pyplot as plt

import send_telegram
from tmax import notify


class TemperatureDisplayTests(unittest.TestCase):
    def test_converts_celsius_to_fahrenheit_for_display(self):
        self.assertEqual(notify.temperature_for_unit(0.0, "F"), 32.0)
        self.assertEqual(notify.temperature_for_unit(25.0, "F"), 77.0)
        self.assertEqual(notify.temperature_for_unit(25.0, "C"), 25.0)

    def test_distribution_axis_and_points_use_fahrenheit(self):
        dist = {
            "buckets": [
                {"low": 25, "high": 26, "prob": 0.4},
                {"low": 26, "high": 27, "prob": 0.6},
            ],
            "quantiles": {50: 26.5},
            "comps": [(26.5, 1.0)],
            "obs_floor": None,
        }
        fig, ax = plt.subplots()
        try:
            notify._draw_dist(ax, dist, "Hoje", {"modelo": 26.0}, 27.0,
                              unit="F")
            self.assertEqual(ax.get_xlabel(), "Faixa da máxima (°F)")
            self.assertAlmostEqual(ax.lines[0].get_xdata()[0], 79.7)
            self.assertAlmostEqual(ax.lines[1].get_xdata()[0], 78.8)
            self.assertAlmostEqual(ax.lines[2].get_xdata()[0], 80.6)
            labels = [tick.get_text() for tick in ax.get_xticklabels()]
            self.assertIn("78–79", labels)
        finally:
            plt.close(fig)

    @patch("tmax.notify.hourly_percentiles")
    def test_hourly_chart_converts_ensemble_and_observation(self, percentiles):
        now = dt.datetime(2026, 7, 26, 12, tzinfo=dt.timezone.utc)
        percentiles.return_value = ([now], [0.0], [10.0], [20.0], [10.0])
        ctx = {
            "station": SimpleNamespace(unit="F"),
            "ens": {"time": [], "members": []},
            "bias": {}, "shift": 0.0, "now": now, "d0": now.date(),
            "obs_today": [{"time": now, "temp": 0.0}],
        }
        fig, ax = plt.subplots()
        try:
            notify._draw_hourly(ax, ctx)
            observed = next(line for line in ax.lines
                            if line.get_label() == "Observado (METAR)")
            self.assertEqual(ax.get_ylabel(), "°F")
            self.assertAlmostEqual(observed.get_ydata()[0], 32.0)
        finally:
            plt.close(fig)

    @patch("send_telegram._peak_hour", return_value=15)
    def test_ceifa_message_uses_station_unit(self, _peak_hour):
        station = SimpleNamespace(
            unit="F", flag="🇺🇸", city="Chicago", icao="KORD")
        ctx = {
            "dist_d0": {"quantiles": {10: 25.0, 50: 26.0, 90: 27.0}},
            "now": dt.datetime(2026, 7, 26, 14, 30),
        }

        message = send_telegram._ceifa_text(
            station, ctx, [("key", "78-79°F", 0.97, 20, 5.0)])

        self.assertIn("Mediana: <b>78.8 °F</b>", message)
        self.assertIn("P10 77.0 · P90 80.6", message)


if __name__ == "__main__":
    unittest.main()
