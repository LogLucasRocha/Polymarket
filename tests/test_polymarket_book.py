import unittest
from unittest.mock import Mock, patch

from tmax import polymarket


class ExecutableBookTests(unittest.TestCase):
    @staticmethod
    def _event():
        return {"rows": [{"label": "30°C", "no_token_id": "no-30"}]}

    @patch("tmax.polymarket.requests.get")
    def test_event_maps_no_outcome_to_its_clob_token(self, get):
        response = Mock()
        response.json.return_value = [{"title": "Toronto", "markets": [{
            "question": "Will the highest temperature in Toronto be 30°C?",
            "groupItemTitle": "30°C",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.03", "0.97"]',
            "clobTokenIds": '["yes-30", "no-30"]',
        }]}]
        get.return_value = response

        event = polymarket.fetch_event("toronto")

        self.assertEqual(event["rows"][0]["no_token_id"], "no-30")
        self.assertEqual(event["rows"][0]["no"], 0.97)

    @patch("tmax.polymarket.requests.post")
    def test_uses_lowest_executable_no_ask_and_its_size(self, post):
        response = Mock()
        response.json.return_value = [{
            "asset_id": "no-30",
            "asks": [
                {"price": "0.980", "size": "20"},
                {"price": "0.970", "size": "12.5"},
            ],
        }]
        post.return_value = response

        event = polymarket.attach_no_best_asks(self._event())

        self.assertEqual(event["rows"][0]["no_ask"], 0.97)
        self.assertEqual(event["rows"][0]["no_ask_size"], 12.5)
        response.raise_for_status.assert_called_once()

    @patch("tmax.polymarket.requests.post")
    def test_no_sell_offer_means_no_executable_price(self, post):
        response = Mock()
        response.json.return_value = [{
            "asset_id": "no-30", "bids": [{"price": "0.97", "size": "50"}],
            "asks": [],
        }]
        post.return_value = response

        event = polymarket.attach_no_best_asks(self._event())

        self.assertIsNone(event["rows"][0]["no_ask"])
        self.assertIsNone(event["rows"][0]["no_ask_size"])


if __name__ == "__main__":
    unittest.main()
