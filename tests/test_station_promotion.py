import unittest

from tmax import config, polymarket


class StationPromotionTests(unittest.TestCase):
    def test_former_observation_cities_are_active(self):
        promoted = {
            "LIMC", "ZHHH", "EDDM", "EFHK", "LLBG", "RPLL",
            "WMKK", "RCSS", "ZGGG", "ZGSZ", "ZUUU", "FACT",
        }

        self.assertTrue(promoted.issubset(config.STATIONS))
        self.assertEqual(config.STATIONS_OBSERVE, {})
        self.assertEqual(len(config.STATIONS), 39)

    def test_fahrenheit_cities_are_active_and_keep_their_unit(self):
        self.assertTrue(set(config.STATIONS_FAHRENHEIT).issubset(config.STATIONS))
        self.assertEqual(len(config.STATIONS_FAHRENHEIT), 9)
        for icao in config.STATIONS_FAHRENHEIT:
            with self.subTest(icao=icao):
                self.assertEqual(config.STATIONS[icao].unit, "F")

    def test_minimum_study_covers_both_units(self):
        # A Ceifa de mínimas roda sobre todas as estações ativas, nas duas
        # unidades (send_telegram/monitor usam set(config.STATIONS)).
        icaos = set(config.STATIONS)

        self.assertEqual({config.STATIONS[icao].unit for icao in icaos},
                         {"C", "F"})

    def test_promoted_city_market_titles_are_recognized(self):
        titles = {
            "LIMC": "Will the highest temperature in Milan be 31°C on July 27?",
            "ZHHH": "Will the highest temperature in Wuhan be 36°C on July 27?",
            "EDDM": "Will the highest temperature in Munich be 25°C on July 27?",
            "EFHK": "Will the highest temperature in Helsinki be 24°C on July 27?",
            "LLBG": "Will the highest temperature in Tel Aviv be 30°C on July 27?",
            "RPLL": "Will the highest temperature in Manila be 33°C on July 27?",
            "WMKK": "Will the highest temperature in Kuala Lumpur be 32°C on July 27?",
            "RCSS": "Will the highest temperature in Taipei be 34°C on July 27?",
            "ZGGG": "Will the highest temperature in Guangzhou be 35°C on July 27?",
            "ZGSZ": "Will the highest temperature in Shenzhen be 34°C on July 27?",
            "ZUUU": "Will the highest temperature in Chengdu be 35°C on July 27?",
            "FACT": "Will the highest temperature in Cape Town be 20°C on July 27?",
        }

        for expected_icao, title in titles.items():
            with self.subTest(icao=expected_icao):
                parsed = polymarket.parse_temp_market(title)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["icao"], expected_icao)


if __name__ == "__main__":
    unittest.main()
