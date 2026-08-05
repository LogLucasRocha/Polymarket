import unittest

import ceifa_monitor as dashboard


class DashboardHourlyTest(unittest.TestCase):
    def setUp(self):
        self.stats = {
            "signals": [
                {"ts": "2026-08-01T13:00:00Z"},  # 10h em Brasília
                {"ts": "2026-08-01T13:05:00Z"},
                {"ts": "2026-08-01T14:00:00Z"},  # 11h em Brasília
                {"ts": "2026-08-02T14:00:00Z"},
            ]
        }

    def test_average_includes_zero_for_hours_without_entry_on_a_day(self):
        averages = dashboard.hourly_average(self.stats)

        self.assertEqual(dashboard.hourly_day_count(self.stats), 2)
        self.assertEqual(averages.loc[10], 1.0)
        self.assertEqual(averages.loc[11], 1.0)
        self.assertEqual(averages.loc[12], 0.0)

    def test_chart_keeps_accumulated_count_in_hover_data(self):
        chart = dashboard.hourly_chart(self.stats)
        tenth_hour = list(chart.data[0].x).index("10h")

        self.assertEqual(chart.data[0].y[tenth_hour], 1.0)
        self.assertEqual(chart.data[0].customdata[tenth_hour][0], 2)
        self.assertEqual(chart.data[0].customdata[tenth_hour][1], 2)


if __name__ == "__main__":
    unittest.main()
