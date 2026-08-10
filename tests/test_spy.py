import datetime as dt
import unittest
from unittest import mock

import pandas as pd

from spy import BAND, MERCADOS, capture, study


class RegistryTests(unittest.TestCase):
    def test_band_is_95_to_998(self):
        self.assertEqual(BAND, (0.95, 0.998))

    def test_bitcoin_market_registered(self):
        self.assertIn("bitcoin", MERCADOS)
        self.assertEqual(MERCADOS["bitcoin"].slug_prefix, "bitcoin-above-on")

    def test_bitcoin_slug_matches_url(self):
        slug = capture.market_slug(
            MERCADOS["bitcoin"].slug_prefix, dt.date(2026, 8, 8))
        self.assertEqual(slug, "bitcoin-above-on-august-8-2026")

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

    def test_spy_day_is_et_calendar(self):
        from spy import market_date
        spy = MERCADOS["spy"]
        # 17:00 UTC = 13:00 ET → ainda dia 8 (calendário ET).
        now = dt.datetime(2026, 8, 8, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(spy, now), dt.date(2026, 8, 8))

    def test_spy_above_is_strikes_closing_16_et(self):
        from spy import market_date
        m = MERCADOS["spy_above"]
        self.assertEqual(m.kind, "strikes")
        self.assertFalse(m.rolling)
        self.assertEqual(
            capture.market_slug(m.slug_prefix, dt.date(2026, 8, 10)),
            "spy-closes-above-on-august-10-2026")
        # fecha 16:00 ET = 20:00 UTC (EDT), calendário ET (não rolling)
        self.assertEqual(
            study._close_utc("spy_above", "2026-08-10"),
            pd.Timestamp("2026-08-10T20:00:00Z"))
        now = dt.datetime(2026, 8, 10, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(market_date(m, now), dt.date(2026, 8, 10))

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
    """Snapshots de 10 min entre 12:00 e 20:00 UTC (fechamento 16:00 EDT)."""
    rows = []
    start = dt.datetime(2026, 8, 7, 12, 0, tzinfo=dt.timezone.utc)
    for i in range(49):                       # 12:00 .. 20:00 (49 pontos)
        ts = start + dt.timedelta(minutes=10 * i)
        hhmm = ts.strftime("%H:%M")
        up = last_up if i == 48 else up_prices.get(hhmm, 0.97)
        rows.append({"ts_utc": ts.isoformat(), "dia": dia,
                     "preco_up": up, "preco_down": round(1 - up, 4)})
    return _frame(rows)


class SpySlugTests(unittest.TestCase):
    def test_slug_matches_polymarket_pattern(self):
        slug = capture.spy_slug(dt.date(2026, 8, 7))
        self.assertEqual(slug, "spy-up-or-down-on-august-7-2026")


class SpyStudyTests(unittest.TestCase):
    def test_no_window_counts_every_ten_minutes(self):
        frame = _day_series({}, last_up=0.999)   # Up sempre 0,97; resolve Up=1
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=None)
        # 48 pontos na faixa (12:00..19:50); 20:00 sai da faixa (0,999).
        self.assertEqual(stats["n"], 48)
        self.assertEqual(stats["wins"], 48)      # Up venceu
        self.assertEqual(stats["by_pick"], {"up": 48, "down": 0})
        self.assertGreater(stats["real_mult"], 1.0)

    def test_h1_window_keeps_only_last_hour(self):
        frame = _day_series({}, last_up=0.999)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(window_hours=1)
        # 19:00, 19:10, 19:20, 19:30, 19:40, 19:50 = 6 parcelas.
        self.assertEqual(stats["n"], 6)

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
        self.assertEqual(stats["by_pick"], {"up": 0, "down": 6})
        self.assertEqual(stats["wins"], 6)       # Down venceu

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
        self.assertEqual(progress["snapshots"], 49)
        self.assertEqual(progress["parcelas"], 48)   # 12:00..19:50 na faixa

    def test_daily_summary_marks_open_day(self):
        frame = _day_series({}, last_up=0.60)   # não resolveu
        with mock.patch.object(study, "_load_market", return_value=frame):
            daily = study.daily_summary()
        self.assertEqual(len(daily), 1)
        row = daily.iloc[0]
        self.assertFalse(bool(row["resolvido"]))
        self.assertEqual(row["resultado"], "Em aberto")
        self.assertEqual(int(row["parcelas"]), 48)

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
        self.assertEqual(len(prices), 49)
        self.assertEqual(set(prices.columns),
                         {"ts", "preco_up", "preco_down", "dia"})
        self.assertEqual(prices["dia"].iloc[0], "2026-08-07")

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
                         "preco_up": 0.9975, "preco_down": 0.0025})  # na faixa
        frame = _frame(rows)
        with mock.patch.object(study, "_load_market", return_value=frame):
            stats = study.simulate(None, "bitcoin")
            daily = study.daily_summary("bitcoin")
            progress = study.today_progress("bitcoin")
        self.assertEqual(stats["n"], 0)                       # nada pontuado
        self.assertEqual(daily.iloc[0]["resultado"], "Em aberto")
        self.assertFalse(progress["resolved"])
        self.assertGreater(progress["parcelas"], 0)           # mas conta parcelas

    def test_rolling_day_resolves_after_it_rolls(self):
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
        # o dia 09 resolveu (Yes 0,97 > 0,5 = venceu), mesmo sem pino no close
        self.assertGreater(stats["n"], 0)
        self.assertEqual(stats["wins"], stats["n"])
        row09 = daily[daily["dia"] == pd.Timestamp("2026-08-09")].iloc[0]
        self.assertTrue(bool(row09["resolvido"]))
        self.assertEqual(row09["resultado"], "Acerto")
        # o dia 10, ainda aberto (relógio < seu fechamento), não pontua
        row10 = daily[daily["dia"] == pd.Timestamp("2026-08-10")].iloc[0]
        self.assertFalse(bool(row10["resolvido"]))


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
