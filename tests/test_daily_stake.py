import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import send_telegram
from tmax import config


class DailyStakeTests(unittest.TestCase):
    def test_automatic_positions_summary_is_disabled(self):
        self.assertFalse(config.POSITIONS_SUMMARY_ENABLED)

    def test_minimum_no_strategy_is_enabled(self):
        self.assertTrue(config.CEIFA_MINIMUM_ENABLED)

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
        station = SimpleNamespace(
            flag="🇨🇦", city="Toronto", icao="CYYZ", unit="C")
        ctx = {
            "dist_d0": {
                "quantiles": {10: 25.0, 50: 26.0, 90: 27.0},
                "comps": [(25.5, 1.0), (28.2, 1.2)],
            },
        }
        text = send_telegram._ceifa_repeat_text(
            station, ctx, [("key", "30°C", 0.97, 25, 10.32)])

        self.assertIn("stake: <b>$10.32</b>", text)
        self.assertIn("Comprar <b>NÃO 30°C</b>", text)
        self.assertIn("MÁXIMA", text)
        self.assertIn("Mediana da máxima: <b>26.0 °C</b>", text)
        self.assertIn("Piso ens. 25.5 °C", text)
        self.assertIn("Teto ens. 28.2 °C", text)

    def test_minimum_purchase_message_is_explicit_and_complete(self):
        station = SimpleNamespace(
            flag="🇬🇧", city="Londres", icao="EGLC", unit="C")
        ctx = {"now": dt.datetime(2026, 7, 31, 4, 5), "dist_d0": {}}
        forecast = {
            "pico_hora": 5, "mediana": 14.0, "p10": 13.2, "p90": 14.8,
            "piso_ens": 12.5, "teto_ens": 15.4,
        }

        text = send_telegram._ceifa_repeat_text(
            station, ctx, [("min:key", "13°C", 0.97, 20, 8.25)],
            extreme="minimum", forecast=forecast)

        self.assertIn("MÍNIMA", text)
        self.assertIn("Comprar <b>NÃO 13°C</b>", text)
        self.assertIn("Mínimo previsto: <b>05h</b>", text)
        self.assertIn("Mediana da mínima: <b>14.0 °C</b>", text)
        self.assertIn("P10 13.2", text)
        self.assertIn("P90 14.8", text)
        self.assertIn("Piso ens. 12.5 °C", text)
        self.assertIn("Teto ens. 15.4 °C", text)

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

    def test_position_cost_uses_invested_value_instead_of_market_value(self):
        positions = [{
            "asset": "token-a", "size": 20, "avgPrice": 0.97,
            "currentValue": 12.0,
        }, {
            "asset": "token-a", "initialValue": 4.5,
            "currentValue": 3.0,
        }]

        result = send_telegram._position_cost_by_token(positions)

        self.assertAlmostEqual(result["token-a"], 23.9)

    def test_blocks_contract_already_at_three_percent_of_capital(self):
        pending = {"A": [("a", "30°C", 0.97, 10)]}

        result = send_telegram._allocate_relative_stakes(
            pending, ["A"], 1000.0, 0.01,
            token_by_contract={"a": "token-a"},
            allocated_by_token={"token-a": 30.0},
            total_capital=1000.0, position_cap_frac=0.03)

        self.assertNotIn("A", result)

    def test_three_relative_parcels_reach_operational_alert_limit(self):
        pending = {"A": [("a", "30°C", 0.97, 10)]}
        target = send_telegram.config.CEIFA_POSITION_TARGET_FRAC

        result = send_telegram._allocate_relative_stakes(
            pending, ["A"], 970.299, 0.01,
            token_by_contract={"a": "token-a"},
            allocated_by_token={"token-a": target * 1000.0},
            total_capital=1000.0, position_cap_frac=target)

        self.assertNotIn("A", result)
        self.assertAlmostEqual(target, 0.029701)

    def test_reduces_last_stake_to_remaining_three_percent_room(self):
        pending = {"A": [("a", "30°C", 0.97, 10)]}

        result = send_telegram._allocate_relative_stakes(
            pending, ["A"], 1000.0, 0.01,
            token_by_contract={"a": "token-a"},
            allocated_by_token={"token-a": 25.0},
            total_capital=1000.0, position_cap_frac=0.03)

        self.assertAlmostEqual(result["A"][0][-1], 5.0)

    def test_blocks_alert_when_token_is_missing_under_position_cap(self):
        pending = {"A": [("a", "30°C", 0.97, 10)]}

        result = send_telegram._allocate_relative_stakes(
            pending, ["A"], 1000.0, 0.01,
            token_by_contract={}, allocated_by_token={},
            total_capital=1000.0, position_cap_frac=0.03)

        self.assertNotIn("A", result)

    @patch("send_telegram.dt.datetime")
    def test_minimum_alert_is_archived_with_its_strategy_and_day(self, now):
        now.now.return_value = dt.datetime(2026, 7, 31, 4, 5)
        pending = {
            "EGLC": [("min:EGLC:2026-07-31:13°C", "13°C",
                      0.97, None, 8.25)],
        }

        rows = send_telegram._ceifa_alert_rows(
            pending, set(), {}, extreme="minimum")

        self.assertEqual(rows[0]["dia"], "2026-07-31")
        self.assertEqual(rows[0]["estrategia"], "ceifa_minima")
        self.assertEqual(rows[0]["extremo"], "minimum")
        self.assertIsNone(rows[0]["volume_disponivel"])

    def test_spy_h1_alert_names_side_filters_and_brasilia_close(self):
        close = dt.datetime(2026, 8, 11, 20, 0, tzinfo=dt.timezone.utc)
        contracts = [("2026-08-11:down", "Down", 0.97, 12,
                      {"pair_sum": 1.04, "close": close}, 8.08)]

        text = send_telegram._spy_h1_text(contracts)

        self.assertIn("SPY UP OR DOWN — H-1", text)
        self.assertIn("Comprar <b>DOWN</b>", text)
        self.assertIn("stake: <b>$8.08</b>", text)
        self.assertIn("104.0¢", text)
        self.assertIn("17:00 de Brasília", text)


if __name__ == "__main__":
    unittest.main()
