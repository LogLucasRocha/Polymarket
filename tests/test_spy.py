import datetime as dt
import unittest
from unittest import mock

import pandas as pd

from spy import (BAND, INTERVAL_MINUTES, MERCADOS, PAIR_ASK_CEILING, capture,
                 study)


class RegistryTests(unittest.TestCase):
    def test_band_is_95_to_995(self):
        self.assertEqual(BAND, (0.95, 0.995))
        self.assertEqual(PAIR_ASK_CEILING, 1.05)

    def test_all_markets_use_995_ceiling(self):
        for market in MERCADOS:
            self.assertEqual(study.price_band(market), (0.95, 0.995))

    def test_bitcoin_market_registered(self):
        self.assertIn("bitcoin", MERCADOS)
        self.assertEqual(MERCADOS["bitcoin"].slug_prefix, "bitcoin-above-on")

    def test_bitcoin_slug_matches_url(self):
        slug = capture.market_slug(
            MERCADOS["bitcoin"].slug_prefix, dt.date(2026, 8, 8))
        self.assertEqual(slug, "bitcoin-above-on-august-8-2026")

    def test_solana_markets_match_urls_and_bitcoin_schedule(self):
        above = MERCADOS["solana"]
        updown = MERCADOS["sol_updown"]
        self.assertEqual(above.kind, "strikes")
        self.assertEqual(updown.kind, "binary")
        for market, expected in (
                (above, "solana-above-on-august-10-2026"),
                (updown, "solana-up-or-down-on-august-10-2026")):
            self.assertTrue(market.rolling)
            self.assertEqual(market.close_hour, 16)
            self.assertEqual(market.tz, "UTC")
            self.assertEqual(
                capture.market_slug(market.slug_prefix, dt.date(2026, 8, 10)),
                expected)
            self.assertEqual(
                study._close_utc(market.key, "2026-08-10"),
                pd.Timestamp("2026-08-10T16:00:00Z"))

    def test_ethereum_markets_match_urls_and_crypto_schedule(self):
        above = MERCADOS["ethereum"]
        updown = MERCADOS["eth_updown"]
        self.assertEqual(above.kind, "strikes")
        self.assertEqual(updown.kind, "binary")
        for market, expected in (
                (above, "ethereum-above-on-august-12-2026"),
                (updown, "ethereum-up-or-down-on-august-12-2026")):
            self.assertTrue(market.rolling)
            self.assertEqual(market.close_hour, 16)
            self.assertEqual(market.tz, "UTC")
            self.assertEqual(
                capture.market_slug(market.slug_prefix, dt.date(2026, 8, 12)),
                expected)
            self.assertEqual(
                study._close_utc(market.key, "2026-08-12"),
                pd.Timestamp("2026-08-12T16:00:00Z"))

    def test_spy_closes_at_16_et(self):
        # SPY: 16:00 ET (EDT em agosto) = 20:00 UTC.
        self.assertEqual(
            study._close_utc("spy", "2026-08-08"),
            pd.Timestamp("2026-08-08T20:00:00Z"))

    def test_bitcoin_closes_at_16_utc(self):
        # Bitcoin: o dia vira/resolve às 16:00 UTC.
        self.assertEqual(
            study._close_utc("bitcoin", "2026-08-09"),
            pd.Timestamp("2026-08-09T16:00:00Z"))

    def test_bitcoin_day_rolls_at_16_utc(self):
        from spy import market_date
        btc = MERCADOS["bitcoin"]
        antes = dt.datetime(2026, 8, 8, 11, 0, tzinfo=dt.timezone.utc)
        depois = dt.datetime(2026, 8, 8, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(btc, antes), dt.date(2026, 8, 8))
        self.assertEqual(market_date(btc, depois), dt.date(2026, 8, 9))

    def test_spy_day_rolls_at_market_close_and_skips_weekend(self):
        from spy import market_date
        spy = MERCADOS["spy"]
        before_close = dt.datetime(2026, 8, 10, 19, 59,
                                   tzinfo=dt.timezone.utc)
        after_close = dt.datetime(2026, 8, 10, 20, 1,
                                  tzinfo=dt.timezone.utc)
        friday_after_close = dt.datetime(2026, 8, 7, 20, 1,
                                         tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(spy, before_close), dt.date(2026, 8, 10))
        self.assertEqual(market_date(spy, after_close), dt.date(2026, 8, 11))
        self.assertEqual(market_date(spy, friday_after_close),
                         dt.date(2026, 8, 10))

    def test_spy_above_is_strikes_closing_16_et(self):
        from spy import market_date
        m = MERCADOS["spy_above"]
        self.assertEqual(m.kind, "strikes")
        self.assertTrue(m.rolling)
        self.assertTrue(m.weekdays_only)
        self.assertEqual(
            capture.market_slug(m.slug_prefix, dt.date(2026, 8, 10)),
            "spy-closes-above-on-august-10-2026")
        # fecha 16:00 ET = 20:00 UTC (EDT) e então avança ao próximo pregão.
        self.assertEqual(
            study._close_utc("spy_above", "2026-08-10"),
            pd.Timestamp("2026-08-10T20:00:00Z"))
        now = dt.datetime(2026, 8, 10, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(m, now), dt.date(2026, 8, 10))

    def test_spy_capture_falls_forward_when_first_date_is_a_holiday(self):
        spy = MERCADOS["spy"]
        now = dt.datetime(2026, 8, 10, 20, 1, tzinfo=dt.timezone.utc)
        entry = {"faixa": "—",
                 "up": {"price": .56, "token_id": "up", "label": "Up"},
                 "down": {"price": .44, "token_id": "down", "label": "Down"}}
        with mock.patch("spy.capture.dt.datetime") as mocked_datetime, \
             mock.patch("spy.capture.fetch_market",
                        side_effect=[None, [entry]]) as fetch, \
             mock.patch("spy.capture._attach_asks"):
            mocked_datetime.now.return_value = now
            records = capture.coletar(spy)
        self.assertEqual(records[0]["dia"], "2026-08-12")
        self.assertIn("august-12-2026", records[0]["slug"])
        self.assertEqual(fetch.call_count, 2)

    def test_btc_updown_is_binary_daily_rolling_at_16_utc(self):
        from spy import market_date
        m = MERCADOS["btc_updown"]
        self.assertEqual(m.kind, "binary")
        self.assertEqual(m.slug_prefix, "bitcoin-up-or-down-on")
        self.assertEqual(
            capture.market_slug(m.slug_prefix, dt.date(2026, 8, 9)),
            "bitcoin-up-or-down-on-august-9-2026")
        self.assertEqual(
            study._close_utc("btc_updown", "2026-08-09"),
            pd.Timestamp("2026-08-09T16:00:00Z"))
        # o dia vira às 16:00 UTC, como o Bitcoin Above
        depois = dt.datetime(2026, 8, 8, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(m, depois), dt.date(2026, 8, 9))


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if "faixa" not in df:
        df["faixa"] = "—"                  # binário: um contrato por dia
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df


def _day_series(up_prices: dict[str, float], last_up: float,
                dia: str = "2026-08-07") -> pd.DataFrame:
    """Snapshots de 5 min entre 12:00 e 20:00 UTC (fechamento 16:00 EDT)."""
    rows = []
    start = dt.datetime(2026, 8, 7, 12, 0, tzinfo=dt.timezone.utc)
    for i in range(97):                       # 12:00 .. 20:00 (97 pontos)
        ts = start + dt.timedelta(minutes=5 * i)
        hhmm = ts.strftime("%H:%M")
        up = last_up if i == 96 else up_prices.get(hhmm, 0.97)
        rows.append({"ts_utc": ts.isoformat(), "dia": dia,
                     "preco_up": up, "preco_down": round(1 - up, 4)})
    return _frame(rows)


class SpySlugTests(unittest.TestCase):
    def test_slug_matches_polymarket_pattern(self):
        slug = capture.spy_slug(dt.date(2026, 8, 7))
        self.assertEqual(slug, "spy-up-or-down-on-august-7-2026")


class SpyStudyTests(unittest.TestCase):
    def test_all_markets_reject_995_boundary(self):
        row = {"preco_up": 0.995, "preco_down": 0.005}
        for market in MERCADOS:
            self.assertIsNone(study._side_in_band(row, market))

    def test_pair_asks_must_sum_to_less_than_105_cents(self):
        base = {"preco_up": 0.97, "preco_down": 0.03}
        self.assertEqual(
            study._side_in_band(dict(base, ask_up=0.99, ask_down=0.059)),
            "up")
        self.assertIsNone(
            study._side_in_band(dict(base, ask_up=0.99, ask_down=0.06)))
        self.assertIsNone(
            study._side_in_band(dict(base, ask_up=1.00, ask_down=0.06)))

    def test_entry_band_prefers_executable_asks_over_reference_prices(self):
        self.assertIsNone(study._side_in_band({
            "preco_up": 0.96, "preco_down": 0.04,
            "ask_up": 0.999, "ask_down": 0.05,
        }))
        self.assertEqual(study._side_in_band({
            "preco_up": 0.90, "preco_down": 0.10,
            "ask_up": 0.99, "ask_down": 0.059,
        }), "up")

    def test_pair_filter_falls_back_to_prices_without_complete_asks(self):
        self.assertIsNone(study._side_in_band({
            "preco_up": 0.97, "preco_down": 0.08,
            "ask_up": 0.98, "ask_down": None,
        }))

    def test_stats_report_net_parcels_blocked_by_pair_filter(self):
        frame = _day_series({}, last_up=0.999)
        frame["ask_up"] = 0.99
        frame["ask_down"] = 0.06
        frame.loc[frame.index[-1], "ask_up"] = 0.999
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["pair_filter_blocked"], 96)

    def test_no_window_counts_every_five_minutes(self):
        self.assertEqual(INTERVAL_MINUTES, 5)
        frame = _day_series({}, last_up=0.999)   # Up sempre 0,97; resolve Up=1
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        # Três parcelas por posição; os demais sinais são vetados.
        self.assertEqual(stats["n"], 3)
        self.assertEqual(stats["wins"], 3)      # Up venceu
        self.assertEqual(stats["by_pick"], {"up": 3, "down": 0})
        self.assertEqual(stats["n_position_cap_blocked"], 93)
        self.assertGreater(stats["real_mult"], 1.0)

    def test_simulation_charges_the_executable_ask(self):
        frame = _day_series({}, last_up=0.999)
        frame["ask_up"] = 0.98
        frame["ask_down"] = 0.06
        frame.loc[frame.index[-1], ["ask_up", "ask_down"]] = [0.999, 0.05]
        with mock.patch.object(
                study.ceifa, "_stats_relative_available_stake",
                return_value={"n": 96, "wins": 96}) as scorer, \
             mock.patch.object(study, "_load_market", return_value=frame):
            study.simulate(window_hours=None)
        signals = scorer.call_args.args[0]
        self.assertEqual(len(signals), 96)
        self.assertTrue(all(signal["price"] == 0.98 for signal in signals))

    def test_stakes_are_one_percent_of_remaining_daily_cash(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        stakes = [signal["stake"] for signal in stats["signals"][:3]]
        self.assertAlmostEqual(stakes[0], 0.01)
        self.assertAlmostEqual(stakes[1], 0.0099)
        self.assertAlmostEqual(stakes[2], 0.009801)
        self.assertAlmostEqual(
            sum(s["stake"] for s in stats["signals"]), 0.029701)

    def test_h1_window_keeps_only_last_hour(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=1)
        # O teto encerra a posição na terceira parcela da H-1.
        self.assertEqual(stats["n"], 3)

    def test_allocates_to_down_when_down_is_in_band(self):
        # Down em 0,97 (na faixa), Up em 0,03; resolve Down=1 (fechou em queda).
        rows = []
        start = dt.datetime(2026, 8, 7, 19, 0, tzinfo=dt.timezone.utc)
        for i in range(6):
            ts = start + dt.timedelta(minutes=10 * i)
            rows.append({"ts_utc": ts.isoformat(), "dia": "2026-08-07",
                         "preco_up": 0.03, "preco_down": 0.97})
        rows.append({"ts_utc": dt.datetime(2026, 8, 7, 20, 0,
                     tzinfo=dt.timezone.utc).isoformat(), "dia": "2026-08-07",
                     "preco_up": 0.002, "preco_down": 0.998})
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        self.assertEqual(stats["by_pick"], {"up": 0, "down": 3})
        self.assertEqual(stats["wins"], 3)       # Down venceu

    def test_unresolved_day_is_skipped(self):
        # Último preço no meio (0,60): dia ainda não resolveu → nada conta.
        frame = _day_series({}, last_up=0.60)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        self.assertEqual(stats["n"], 0)

    def test_today_progress_counts_open_parcelas(self):
        # Dia em aberto (último preço no meio): conta parcelas, resolved=False.
        frame = _day_series({}, last_up=0.60)
        with mock.patch.object(study, "_load_market", return_value=frame):
            progress = study.today_progress()
        self.assertEqual(progress["day"], "2026-08-07")
        self.assertFalse(progress["resolved"])
        self.assertEqual(progress["snapshots"], 97)
        self.assertEqual(progress["parcelas"], 3)

    def test_daily_summary_marks_open_day(self):
        frame = _day_series({}, last_up=0.60)   # não resolveu
        with mock.patch.object(study, "_load_market", return_value=frame):
            daily = study.daily_summary()
        self.assertEqual(len(daily), 1)
        row = daily.iloc[0]
        self.assertFalse(bool(row["resolvido"]))
        self.assertEqual(row["resultado"], "Em aberto")
        self.assertEqual(int(row["parcelas"]), 3)

    def test_daily_summary_marks_resolved_win(self):
        frame = _day_series({}, last_up=0.999)  # resolveu Up=1
        with mock.patch.object(study, "_load_market", return_value=frame):
            daily = study.daily_summary()
        row = daily.iloc[0]
        self.assertTrue(bool(row["resolvido"]))
        self.assertEqual(row["resultado"], "Acerto")

    def test_latest_prices_returns_series_of_latest_day(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            prices = study.latest_prices()
        self.assertEqual(len(prices), 97)
        self.assertEqual(set(prices.columns),
                         {"ts", "preco_up", "preco_down", "dia"})
        self.assertEqual(prices["dia"].iloc[0], "2026-08-07")

    def test_latest_prices_prefers_executable_asks(self):
        frame = _day_series({}, last_up=0.999)
        frame["ask_up"] = 0.981
        frame["ask_down"] = 0.061
        with mock.patch.object(study, "_load_market", return_value=frame):
            prices = study.latest_prices()
        self.assertEqual(float(prices.iloc[0]["preco_up"]), 0.981)
        self.assertEqual(float(prices.iloc[0]["preco_down"]), 0.061)

    def test_latest_strikes_displays_buy_prices_and_marks_from_asks(self):
        frame = _frame([{
            "ts_utc": "2026-08-12T23:46:41+00:00", "dia": "2026-08-13",
            "faixa": "$800", "preco_up": 0.02, "preco_down": 0.98,
            "ask_up": 0.039, "ask_down": 0.999,
        }])
        with mock.patch.object(study, "_load_market", return_value=frame):
            strikes = study.latest_strikes("spy_above")
        self.assertEqual(float(strikes.iloc[0]["preco_up"]), 0.039)
        self.assertEqual(float(strikes.iloc[0]["preco_down"]), 0.999)
        self.assertFalse(bool(strikes.iloc[0]["na_faixa"]))

    def test_price_days_and_prices_for_previous_day(self):
        older = _day_series({}, last_up=0.999, dia="2026-08-06")
        latest = _day_series({}, last_up=0.60, dia="2026-08-07")
        frame = pd.concat([latest, older], ignore_index=True)
        with mock.patch.object(study, "_load_market", return_value=frame):
            self.assertEqual(study.price_days(),
                             ["2026-08-06", "2026-08-07"])
            prices = study.prices_for_day(day="2026-08-06")
        self.assertEqual(len(prices), 97)
        self.assertEqual(prices["dia"].unique().tolist(), ["2026-08-06"])

    def test_prices_for_unknown_day_is_empty(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            prices = study.prices_for_day(day="2026-08-05")
        self.assertTrue(prices.empty)

    def test_resolved_day_without_band_entry_has_zero_parcelas(self):
        # Mercado já resolvido (Up=1.0 o tempo todo): fora da faixa → 0 parcelas.
        rows = []
        import datetime as dt
        start = dt.datetime(2026, 8, 8, 0, 2, tzinfo=dt.timezone.utc)
        for i in range(10):
            ts = start + dt.timedelta(minutes=5 * i)
            rows.append({"ts_utc": ts.isoformat(), "dia": "2026-08-07",
                         "preco_up": 1.0, "preco_down": 0.0})
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            daily = study.daily_summary()
        row = daily.iloc[0]
        self.assertEqual(int(row["parcelas"]), 0)
        self.assertTrue(bool(row["resolvido"]))
        self.assertEqual(row["resultado"], "Sem entrada")

    def test_empty_lake_returns_zeroed_stats(self):
        with mock.patch.object(study, "_load_market",
                               return_value=pd.DataFrame()):
            stats = study.simulate()
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["real_mult"], 1.0)

    def test_production_is_only_h1(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.run_production()
            daily = study.daily_summary(window_hours=1)
        self.assertEqual(stats["window_hours"], 1)
        self.assertEqual(stats["archive_kind"], "spy")
        self.assertEqual(stats["n"], 3)
        self.assertEqual(int(daily.iloc[0]["parcelas"]), 3)

    def test_live_candidate_requires_recent_h1_executable_snapshot(self):
        frame = _frame([{
            "ts_utc": "2026-08-07T19:30:00+00:00",
            "dia": "2026-08-07", "preco_up": 0.50,
            "preco_down": 0.50, "ask_up": 0.99, "ask_down": 0.05,
            "up_label": "Up", "down_label": "Down",
            "up_token_id": "spy-up-token", "ask_up_volume": 8,
        }])
        now = dt.datetime(2026, 8, 7, 19, 31, tzinfo=dt.timezone.utc)
        with mock.patch.object(study, "_load_market", return_value=frame):
            candidate = study.production_candidate(now)
        self.assertEqual(candidate["side"], "up")
        self.assertEqual(candidate["price"], 0.99)
        self.assertEqual(candidate["pair_sum"], 1.04)
        self.assertEqual(candidate["token_id"], "spy-up-token")

    def test_live_candidate_rejects_stale_snapshot(self):
        frame = _frame([{
            "ts_utc": "2026-08-07T19:00:00+00:00",
            "dia": "2026-08-07", "preco_up": 0.97,
            "preco_down": 0.03,
        }])
        now = dt.datetime(2026, 8, 7, 19, 20, tzinfo=dt.timezone.utc)
        with mock.patch.object(study, "_load_market", return_value=frame):
            self.assertIsNone(study.production_candidate(now))


if __name__ == "__main__":
    unittest.main()


class StrikesTests(unittest.TestCase):
    def _frame(self):
        import datetime as dt
        rows = []
        start = dt.datetime(2026, 8, 9, 10, 0, tzinfo=dt.timezone.utc)
        for i in range(20):                       # 10:00 .. 13:10 UTC
            iso = (start + dt.timedelta(minutes=10 * i)).isoformat()
            rows.append({"ts_utc": iso, "dia": "2026-08-09", "faixa": "64000",
                         "preco_up": 0.97, "preco_down": 0.03})   # Yes na faixa
            rows.append({"ts_utc": iso, "dia": "2026-08-09", "faixa": "66000",
                         "preco_up": 0.10, "preco_down": 0.90})   # fora da faixa
        res = dt.datetime(2026, 8, 9, 16, 0, tzinfo=dt.timezone.utc).isoformat()
        rows.append({"ts_utc": res, "dia": "2026-08-09", "faixa": "64000",
                     "preco_up": 0.999, "preco_down": 0.001})     # Yes venceu
        rows.append({"ts_utc": res, "dia": "2026-08-09", "faixa": "66000",
                     "preco_up": 0.001, "preco_down": 0.999})
        return _frame(rows)

    def test_in_band_strike_becomes_signal(self):
        with mock.patch.object(study, "_load_market", return_value=self._frame()):
            stats = study.simulate(None, "bitcoin")
        self.assertGreater(stats["n"], 0)              # 64000 Yes vira parcela
        self.assertEqual(stats["wins"], stats["n"])    # Yes venceu
        self.assertEqual(stats["by_pick"]["down"], 0)  # 66000 nunca na faixa

    def test_daily_summary_aggregates_strikes(self):
        with mock.patch.object(study, "_load_market", return_value=self._frame()):
            daily = study.daily_summary("bitcoin")
        self.assertEqual(len(daily), 1)
        self.assertGreater(int(daily.iloc[0]["parcelas"]), 0)
        self.assertEqual(daily.iloc[0]["resultado"], "Acerto")

    def test_open_day_deep_itm_strike_is_not_scored(self):
        # Dia ainda aberto (sem snapshot no fechamento): um strike fundo no
        # dinheiro (Yes ~100¢ na abertura) NÃO pode pontuar como resolvido.
        rows = []
        start = dt.datetime(2026, 8, 9, 10, 0, tzinfo=dt.timezone.utc)
        for i in range(6):                     # 10:00..10:50, longe do 16:00 UTC
            iso = (start + dt.timedelta(minutes=10 * i)).isoformat()
            rows.append({"ts_utc": iso, "dia": "2026-08-09", "faixa": "62000",
                         "preco_up": 0.994, "preco_down": 0.006})  # na faixa
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(None, "bitcoin")
            daily = study.daily_summary("bitcoin")
            progress = study.today_progress("bitcoin")
        self.assertEqual(stats["n"], 0)                       # nada pontuado
        self.assertEqual(daily.iloc[0]["resultado"], "Em aberto")
        self.assertFalse(progress["resolved"])
        self.assertGreater(progress["parcelas"], 0)           # mas conta parcelas

    def test_rolling_day_does_not_guess_resolution_after_it_rolls(self):
        # Bitcoin (rolling): o dia vira às 16:00 UTC e NUNCA há snapshot depois
        # dele para o próprio dia — o último é ~15:50. Assim que a captura do dia
        # seguinte aparece (relógio > fechamento), o dia fechado deve resolver
        # pelo último preço pré-fechamento. Era o bug do "eterno em aberto".
        rows = []
        start = dt.datetime(2026, 8, 9, 10, 0, tzinfo=dt.timezone.utc)
        for i in range(36):                       # 10:00 .. 15:50 UTC, na faixa
            iso = (start + dt.timedelta(minutes=10 * i)).isoformat()
            rows.append({"ts_utc": iso, "dia": "2026-08-09", "faixa": "64000",
                         "preco_up": 0.97, "preco_down": 0.03})
        # o dia rolou: primeiro snapshot do dia seguinte, já depois das 16:00.
        roll = dt.datetime(2026, 8, 9, 16, 10, tzinfo=dt.timezone.utc).isoformat()
        rows.append({"ts_utc": roll, "dia": "2026-08-10", "faixa": "64000",
                     "preco_up": 0.55, "preco_down": 0.45})   # dia novo, aberto
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(None, "bitcoin")
            daily = study.daily_summary("bitcoin")
        # Sem a resolucao oficial, o ultimo preco pre-close nao pontua.
        self.assertEqual(stats["n"], 0)
        row09 = daily[daily["dia"] == pd.Timestamp("2026-08-09")].iloc[0]
        self.assertFalse(bool(row09["resolvido"]))
        self.assertEqual(row09["resultado"], "Em aberto")
        # o dia 10, ainda aberto (relógio < seu fechamento), não pontua
        row10 = daily[daily["dia"] == pd.Timestamp("2026-08-10")].iloc[0]
        self.assertFalse(bool(row10["resolvido"]))

    def test_rolling_day_uses_explicit_official_resolution(self):
        rows = [{
            "ts_utc": "2026-08-13T15:00:00+00:00", "dia": "2026-08-13",
            "faixa": "â€”", "preco_up": .98, "preco_down": .02,
        }, {
            "ts_utc": "2026-08-13T16:00:00+00:00", "dia": "2026-08-13",
            "faixa": "â€”", "preco_up": 0., "preco_down": 1.,
            "resolucao_oficial": True,
        }, {
            "ts_utc": "2026-08-13T16:10:00+00:00", "dia": "2026-08-14",
            "faixa": "â€”", "preco_up": .5, "preco_down": .5,
        }]
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(1, "btc_updown")
            daily = study.daily_summary("btc_updown", 1)
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["wins"], 0)
        day = daily[daily["dia"] == pd.Timestamp("2026-08-13")].iloc[0]
        self.assertEqual(day["resultado"], "Erro")


class ArchiveByCloseTests(unittest.TestCase):
    def test_closed_day_archived_open_day_stays_in_buffer(self):
        import tempfile
        from pathlib import Path
        from tmax import config as cfg
        btc = MERCADOS["bitcoin"]                 # rolling, fecha 16:00 UTC
        recs = [
            {"ts_utc": "2020-01-01T10:00:00+00:00", "dia": "2020-01-01",
             "faixa": "—", "preco_up": 0.97, "preco_down": 0.03},   # fechado
            {"ts_utc": "2099-01-01T10:00:00+00:00", "dia": "2099-01-01",
             "faixa": "—", "preco_up": 0.97, "preco_down": 0.03},   # aberto
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(cfg, "ROOT", root):
                capture.salvar(btc, recs)
                arch = root / "dados_bitcoin" / "mercado" / "2020-01-01.parquet"
                self.assertTrue(arch.exists())        # fechado → permanente já
                self.assertFalse(
                    (root / "dados_bitcoin" / "mercado"
                     / "2099-01-01.parquet").exists())
                buf = (root / "data_bitcoin" / "mercado.jsonl").read_text(
                    encoding="utf-8")
                self.assertIn("2099-01-01", buf)      # aberto fica no buffer
                self.assertNotIn("2020-01-01", buf)   # fechado saiu do buffer
