import unittest

import ceifa_monitor as dashboard


class DashboardHourlyTest(unittest.TestCase):
    def setUp(self):
        self.stats = {
            "signals": [
                {"ts": "2026-08-01T13:00:00Z", "extreme": "maximum"},  # 10h BR
                {"ts": "2026-08-01T13:05:00Z", "extreme": "minimum"},  # 10h BR
                {"ts": "2026-08-01T14:00:00Z", "extreme": "maximum"},  # 11h BR
                {"ts": "2026-08-02T14:00:00Z", "extreme": "maximum"},  # 11h BR
            ]
        }

    def test_average_includes_zero_for_hours_without_entry_on_a_day(self):
        averages = dashboard.hourly_average(self.stats)

        self.assertEqual(dashboard.hourly_day_count(self.stats), 2)
        self.assertEqual(averages.loc[10], 1.0)
        self.assertEqual(averages.loc[11], 1.0)
        self.assertEqual(averages.loc[12], 0.0)

    def test_hourly_chart_stacks_by_extreme(self):
        piv = dashboard.hourly_by_extreme(self.stats)
        # 10h BR: 1 máxima + 1 mínima; 11h BR: 2 máximas (÷ 2 dias).
        self.assertEqual(piv.loc[10, "Máxima"], 0.5)
        self.assertEqual(piv.loc[10, "Mínima"], 0.5)
        self.assertEqual(piv.loc[11, "Máxima"], 1.0)

        chart = dashboard.hourly_chart(self.stats)
        self.assertEqual({trace.name for trace in chart.data},
                         {"Máxima", "Mínima"})
        self.assertEqual(chart.layout.barmode, "stack")


if __name__ == "__main__":
    unittest.main()
