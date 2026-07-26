import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import send_telegram


class DailyStakeTests(unittest.TestCase):
    @patch("send_telegram.polymarket.fetch_portfolio_capital")
    def test_calculates_one_percent_on_first_round_of_brazil_day(
            self, capital):
        capital.return_value = {
            "free_pusd": 410.12, "positions_value": 618.42,
            "capital": 1028.54,
        }
        now = dt.datetime(2026, 7, 27, 0, 3,
                          tzinfo=ZoneInfo("America/Sao_Paulo"))

        state = send_telegram._ensure_daily_stake(
            "0x" + "1" * 40, {}, now=now)

        self.assertEqual(state["day"], "2026-07-27")
        self.assertAlmostEqual(state["stake"], 10.2854)

    @patch("send_telegram.polymarket.fetch_portfolio_capital")
    def test_does_not_recalculate_again_on_same_day(self, capital):
        previous = {"day": "2026-07-27", "capital": 1000, "stake": 10}
        now = dt.datetime(2026, 7, 27, 15,
                          tzinfo=ZoneInfo("America/Sao_Paulo"))

        state = send_telegram._ensure_daily_stake(
            "0x" + "1" * 40, previous, now=now)

        self.assertIs(state, previous)
        capital.assert_not_called()

    def test_enforces_five_minutes_between_contract_alerts(self):
        now = dt.datetime(2026, 7, 26, 13, 5, tzinfo=dt.timezone.utc)
        self.assertFalse(send_telegram._ceifa_send_due(
            "2026-07-26T13:01:00+00:00", now, 5))
        self.assertTrue(send_telegram._ceifa_send_due(
            "2026-07-26T13:00:00+00:00", now, 5))

    def test_purchase_message_contains_stake_per_contract(self):
        station = SimpleNamespace(flag="🇨🇦", city="Toronto", icao="CYYZ")
        text = send_telegram._ceifa_repeat_text(
            station, [("key", "30°C", 0.97, 25)], 10.32)

        self.assertIn("Stake por contrato: <b>$10.32</b>", text)
        self.assertIn("Comprar <b>NÃO 30°C</b>", text)


if __name__ == "__main__":
    unittest.main()
