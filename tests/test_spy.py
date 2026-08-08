import datetime as dt
import unittest
from unittest import mock

import pandas as pd

from spy import capture, study


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
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

    def test_empty_lake_returns_zeroed_stats(self):
        with mock.patch.object(study, "_load_market",
                               return_value=pd.DataFrame()):
            stats = study.simulate()
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["real_mult"], 1.0)


if __name__ == "__main__":
    unittest.main()
