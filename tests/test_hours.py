import unittest
from datetime import datetime

from app.hours import is_open, minutes_since_opening

SAT_0900 = datetime.fromisoformat("2026-10-10T09:00:00+05:30")
SAT_1810 = datetime.fromisoformat("2026-10-10T18:10:00+05:30")


class HoursTest(unittest.TestCase):
    def test_plain_window(self):
        self.assertTrue(is_open("09:00-18:00", SAT_0900))
        self.assertFalse(is_open("18:00-23:59", SAT_0900))
        self.assertTrue(is_open("18:00-23:59", SAT_1810))

    def test_always_open_and_day_ranges(self):
        self.assertTrue(is_open("24/7", SAT_0900))
        self.assertFalse(is_open("Mo-Fr 09:00-18:00", SAT_0900))  # it is a Saturday
        self.assertTrue(is_open("Mo-Sa 09:00-18:00", SAT_0900))

    def test_unknown_specs_are_unknown_not_guessed(self):
        self.assertIsNone(is_open(None, SAT_0900))
        self.assertIsNone(is_open("sunrise-sunset", SAT_0900))
        self.assertIsNone(is_open("Mo-Fr 09:00-12:00,14:00-18:00; PH off", SAT_0900))

    def test_minutes_since_opening(self):
        self.assertEqual(round(minutes_since_opening("18:00-23:59", SAT_1810)), 10)
        self.assertIsNone(minutes_since_opening("18:00-23:59", SAT_0900))


if __name__ == "__main__":
    unittest.main()
