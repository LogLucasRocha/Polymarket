import unittest
from unittest import mock

import pandas as pd

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

    def test_hourly_chart_stacks_hypothesis_by_market_side(self):
        stats = {"signals": [
            {"ts": "2026-08-01T13:00:00Z", "pick": "up"},
            {"ts": "2026-08-01T13:05:00Z", "pick": "down"},
            {"ts": "2026-08-02T13:00:00Z", "pick": "up"},
        ]}

        piv = dashboard.hourly_by_category(stats, ("Up", "Down"))
        self.assertEqual(piv.loc[10, "Up"], 1.0)
        self.assertEqual(piv.loc[10, "Down"], 0.5)

        chart = dashboard.hourly_chart(stats, ("Up", "Down"))
        self.assertEqual({trace.name for trace in chart.data}, {"Up", "Down"})
        self.assertEqual(chart.layout.barmode, "stack")

    def test_consolidated_chart_names_spy_instead_of_other(self):
        stats = {"signals": [
            {"ts": "2026-08-01T13:00:00Z", "extreme": "spy"},
            {"ts": "2026-08-01T13:05:00Z", "extreme": "maximum"},
        ]}

        piv = dashboard.hourly_by_category(stats)
        chart = dashboard.hourly_chart(stats)

        self.assertEqual(piv.loc[10, "SPY Up or Down"], 1.0)
        self.assertEqual({trace.name for trace in chart.data},
                         {"SPY Up or Down", "Máxima"})
        self.assertNotIn("Outra", {trace.name for trace in chart.data})

    @mock.patch.object(dashboard.st, "caption")
    @mock.patch.object(dashboard.st, "markdown")
    def test_page_context_has_no_ceifa_monitor_banner(self, markdown, caption):
        dashboard.hero("Produção consolidada")

        caption.assert_called_once_with("Produção consolidada")
        markdown.assert_not_called()

    def test_mean_daily_return_averages_resolved_days_with_entries(self):
        stats = {"per_day": [
            {"day": "2026-08-08", "ret": 0.01},
            {"day": "2026-08-09", "ret": -0.004},
            {"day": "2026-08-10", "ret": 0.006},
        ]}
        self.assertAlmostEqual(dashboard.mean_daily_return(stats), 0.004)

    def test_mean_daily_return_is_empty_without_days(self):
        self.assertIsNone(dashboard.mean_daily_return({"per_day": []}))

    def test_mean_daily_parcels_includes_resolved_days_without_entries(self):
        self.assertEqual(dashboard.mean_daily_parcels({"n": 14}, 2), 7.0)
        self.assertIsNone(dashboard.mean_daily_parcels({"n": 14}, 0))

    def test_brasilia_time_labels_convert_from_utc(self):
        stamps = pd.Series(["2026-08-11T00:50:00Z"])
        labels = dashboard.brasilia_time_labels(stamps)
        self.assertEqual(labels.tolist(), ["10/08/2026 21:50"])


if __name__ == "__main__":
    unittest.main()
