import datetime as dt
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import send_telegram


class DailyStakeTests(unittest.TestCase):
    @patch("send_telegram.notify.send_message")
    @patch("send_telegram.polymarket.fetch_portfolio_capital")
    def test_announces_one_percent_on_first_round_of_brazil_day(
            self, capital, send):
        capital.return_value = {
            "free_pusd": 410.12, "positions_value": 618.42,
            "capital": 1028.54,
        }
        now = dt.datetime(2026, 7, 27, 0, 3,
                          tzinfo=ZoneInfo("America/Sao_Paulo"))

        state = send_telegram._ensure_daily_stake(
            "token", "chat", "0x" + "1" * 40, {}, now=now)

        self.assertEqual(state["day"], "2026-07-27")
        self.assertAlmostEqual(state["stake"], 10.2854)
        self.assertIn("$10.29", send.call_args.args[2])

    @patch("send_telegram.notify.send_message")
    @patch("send_telegram.polymarket.fetch_portfolio_capital")
    def test_does_not_recalculate_again_on_same_day(self, capital, send):
        previous = {"day": "2026-07-27", "capital": 1000, "stake": 10}
        now = dt.datetime(2026, 7, 27, 15,
                          tzinfo=ZoneInfo("America/Sao_Paulo"))

        state = send_telegram._ensure_daily_stake(
            "token", "chat", "0x" + "1" * 40, previous, now=now)

        self.assertIs(state, previous)
        capital.assert_not_called()
        send.assert_not_called()

    def test_enforces_five_minutes_between_contract_alerts(self):
        now = dt.datetime(2026, 7, 26, 13, 5, tzinfo=dt.timezone.utc)
        self.assertFalse(send_telegram._ceifa_send_due(
            "2026-07-26T13:01:00+00:00", now, 5))
        self.assertTrue(send_telegram._ceifa_send_due(
            "2026-07-26T13:00:00+00:00", now, 5))


if __name__ == "__main__":
    unittest.main()
