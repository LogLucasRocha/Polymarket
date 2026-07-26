import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import send_telegram


class DailyStakeTests(unittest.TestCase):
    @patch("send_telegram.polymarket.fetch_pusd_balance", return_value=410.12)
    def test_uses_one_percent_of_current_free_balance(self, balance):
        free, stake = send_telegram._current_ceifa_stake(
            "0x" + "1" * 40)

        self.assertEqual(free, 410.12)
        self.assertAlmostEqual(stake, 4.1012)
        balance.assert_called_once()

    def test_enforces_five_minutes_between_contract_alerts(self):
        now = dt.datetime(2026, 7, 26, 13, 5, tzinfo=dt.timezone.utc)
        self.assertFalse(send_telegram._ceifa_send_due(
            "2026-07-26T13:01:00+00:00", now, 5))
        self.assertTrue(send_telegram._ceifa_send_due(
            "2026-07-26T13:00:00+00:00", now, 5))

    def test_purchase_message_contains_stake_per_contract(self):
        station = SimpleNamespace(flag="🇨🇦", city="Toronto", icao="CYYZ")
        text = send_telegram._ceifa_repeat_text(
            station, [("key", "30°C", 0.97, 25, 10.32)])

        self.assertIn("stake: <b>$10.32</b>", text)
        self.assertIn("Comprar <b>NÃO 30°C</b>", text)

    def test_allocates_each_simultaneous_signal_from_remaining_cash(self):
        stations = [SimpleNamespace(icao="A"), SimpleNamespace(icao="B")]
        pending = {
            "A": [("a", "30°C", 0.97, 10), ("b", "31°C", 0.98, 10)],
            "B": [("c", "32°C", 0.96, 10)],
        }

        result = send_telegram._allocate_relative_stakes(
            pending, stations, 1000.0, 0.01)

        self.assertAlmostEqual(result["A"][0][-1], 10.0)
        self.assertAlmostEqual(result["A"][1][-1], 9.9)
        self.assertAlmostEqual(result["B"][0][-1], 9.801)


if __name__ == "__main__":
    unittest.main()
