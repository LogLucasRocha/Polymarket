import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from spy import MERCADOS
from spy import backfill


class SpyBackfillTests(unittest.TestCase):
    def test_aligns_token_buckets_with_small_timestamp_drift(self):
        aligned = backfill._align(
            [{"t": 100, "p": .52}, {"t": 400, "p": .60}],
            [{"t": 103, "p": .48}, {"t": 398, "p": .40}])
        self.assertEqual(aligned["preco_up"].tolist(), [.52, .60])
        self.assertEqual(aligned["preco_down"].tolist(), [.48, .40])

    def test_builds_auditable_records_without_retroactive_book(self):
        event = {"markets": [{
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["up-token", "down-token"]',
            "outcomePrices": '["1", "0"]',
        }]}
        histories = {
            "up-token": [{"t": 1786549813, "p": .99}],
            "down-token": [{"t": 1786549812, "p": .01}],
        }
        with mock.patch.object(backfill, "_event", return_value=event), \
             mock.patch.object(backfill, "_history",
                               side_effect=lambda token, *_: histories[token]):
            records = backfill.build_records(
                MERCADOS["eth_updown"], dt.date(2026, 8, 12))
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0]["livro_consultado"])
        self.assertTrue(records[0]["historico_reconstruido"])
        self.assertEqual(records[0]["fonte_historica"], "clob.prices-history")
        self.assertTrue(records[1]["resolucao_oficial"])
        self.assertEqual(records[1]["fonte_historica"], "gamma.outcomePrices")

    def test_real_snapshot_wins_over_history_in_same_bucket(self):
        day = dt.date(2026, 8, 12)
        historical = [{
            "ts_utc": "2026-08-12T14:55:12+00:00", "dia": str(day),
            "faixa": "—", "preco_up": .98, "preco_down": .02,
            "historico_reconstruido": True,
        }]
        real = {
            "ts_utc": "2026-08-12T14:55:08+00:00", "dia": str(day),
            "faixa": "—", "preco_up": .984, "preco_down": .016,
            "historico_reconstruido": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "dados_eth_updown" / "mercado" / f"{day}.parquet"
            path.parent.mkdir(parents=True)
            buffer = root / "data_eth_updown" / "mercado.jsonl"
            buffer.parent.mkdir(parents=True)
            buffer.write_text(json.dumps(real) + "\n", encoding="utf-8")
            backfill.save_records(MERCADOS["eth_updown"], day, historical, root)
            saved = pd.read_parquet(path)
        self.assertEqual(len(saved), 1)
        self.assertAlmostEqual(float(saved.iloc[0]["preco_up"]), .984)


if __name__ == "__main__":
    unittest.main()
