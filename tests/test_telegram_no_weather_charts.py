import types
import unittest
from unittest import mock

import send_telegram


class TelegramWithoutWeatherChartsTests(unittest.TestCase):
    @mock.patch.object(send_telegram, "_ceifa_text", return_value="alerta")
    @mock.patch.object(send_telegram.notify, "send_photo")
    @mock.patch.object(send_telegram.notify, "send_message")
    def test_ceifa_sends_text_without_weather_chart(
            self, send_message, send_photo, _ceifa_text):
        station = types.SimpleNamespace(city="Cidade", icao="TEST")

        send_telegram._send_ceifa_block(
            "token", "chat", station, {}, [], extreme="maximum")
        send_telegram._send_ceifa_block(
            "token", "chat", station, {}, [], extreme="minimum",
            member_values=[10.0])

        self.assertEqual(send_message.call_count, 2)
        send_photo.assert_not_called()

    @mock.patch.object(send_telegram.notify, "station_hourly_lines",
                       return_value="resumo horário")
    @mock.patch.object(send_telegram.notify, "send_photo")
    @mock.patch.object(send_telegram.notify, "send_message")
    def test_station_sends_hourly_text_without_weather_chart(
            self, send_message, send_photo, _hourly):
        station = types.SimpleNamespace(city="Cidade", icao="TEST")
        ctx = {"tmax_locked": True, "d0": "2026-08-13"}

        send_telegram._send_station_block(
            "token", "chat", station, ctx, [], {}, lambda *_: 0.5,
            lambda *_: 0.5)

        send_message.assert_called_once_with(
            "token", "chat", "resumo horário")
        send_photo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
