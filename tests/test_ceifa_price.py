import unittest

from tmax import ceifa


class CeifaPriceBoundaryTest(unittest.TestCase):
    def test_float_noise_does_not_include_lower_boundary(self):
        value = 0.9500000000000001

        self.assertEqual(ceifa.normalize_market_price(value), 0.95)
        self.assertFalse(ceifa.is_ceifa_price(value))

    def test_float_noise_does_not_include_upper_boundary(self):
        value = 0.9950000000000001

        self.assertEqual(ceifa.normalize_market_price(value), 0.995)
        self.assertFalse(ceifa.is_ceifa_price(value))

    def test_price_strictly_inside_band_remains_eligible(self):
        self.assertTrue(ceifa.is_ceifa_price(0.96))
        self.assertTrue(ceifa.is_ceifa_price(0.9505))


if __name__ == "__main__":
    unittest.main()
