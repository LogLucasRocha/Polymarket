import unittest

import pandas as pd

from lowtemp import backfill_taf


class TafBackfillTests(unittest.TestCase):
    def test_selects_only_forecast_used_by_h1_offer(self):
        market = pd.DataFrame([{
            "ts_utc": "2026-08-01T09:01:00Z", "icao": "KMIA",
            "dia": "2026-08-01", "faixa": "74-75F",
            "preco_nao": .97, "preco_sim": .03,
        }])
        market["ts"] = pd.to_datetime(market["ts_utc"], utc=True)
        forecasts = pd.DataFrame([{
            "ts_utc": "2026-08-01T08:56:00Z", "icao": "KMIA",
            "dia": "2026-08-01", "pico_hora": 6,
        }])
        forecasts["ts"] = pd.to_datetime(forecasts["ts_utc"], utc=True)

        query_at, targets = backfill_taf._forecast_targets(
            market, forecasts)

        bucket = ("KMIA", pd.Timestamp("2026-08-01T09:00:00Z"))
        self.assertEqual(query_at[bucket], market.iloc[0]["ts"])
        self.assertEqual(targets[bucket], {(
            "KMIA", "2026-08-01", "2026-08-01T08:56:00Z")})

    def test_reconstructs_vcts_without_using_later_weather(self):
        bucket = ("KMIA", pd.Timestamp("2026-08-01T09:00:00Z"))
        targets = {bucket: {(
            "KMIA", "2026-08-01", "2026-08-01T08:56:00Z")}}
        tafs = {bucket: {
            "rawTAF": "TAF KMIA ... VCTS",
            "fcsts": [{
                "timeFrom": int(pd.Timestamp(
                    "2026-08-01T18:00:00Z").timestamp()),
                "timeTo": int(pd.Timestamp(
                    "2026-08-02T01:00:00Z").timestamp()),
                "wxString": "VCTS", "fcstChange": "FM",
            }],
        }}

        updates = backfill_taf.build_updates(targets, tafs)
        row = updates[(
            "KMIA", "2026-08-01", "2026-08-01T08:56:00Z")]

        self.assertTrue(row["taf_convective_blocked"])
        self.assertEqual(row["taf_convective_codes"], "VCTS")


if __name__ == "__main__":
    unittest.main()
