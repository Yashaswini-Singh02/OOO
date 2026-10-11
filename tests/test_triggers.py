import unittest

from app import triggers

FONTAINHAS = {"id": "fontainhas", "name": "Fontainhas", "lat": 15.4989, "lng": 73.8313}
MARKET = {"id": "market", "name": "Night market", "lat": 15.5667, "lng": 73.7667, "hours": "18:00-23:59"}


def at(lat, lng, time, weather="clear"):
    return {"lat": lat, "lng": lng, "now": f"2026-10-10T{time}:00+05:30", "weather": weather}


class TriggerTest(unittest.TestCase):
    def test_near_saved_spot(self):
        self.assertTrue(triggers.near_saved_spot(at(15.5005, 73.829, "09:20"), [FONTAINHAS]))
        self.assertFalse(triggers.near_saved_spot(at(15.4827, 73.8073, "09:45"), [FONTAINHAS]))

    def test_place_just_opened(self):
        near_market = (15.566, 73.765)
        self.assertTrue(triggers.place_just_opened(at(*near_market, "18:10"), [MARKET]))
        self.assertFalse(triggers.place_just_opened(at(*near_market, "19:30"), [MARKET]))  # opened long ago
        self.assertFalse(triggers.place_just_opened(at(*near_market, "17:50"), [MARKET]))  # not open yet
        self.assertFalse(triggers.place_just_opened(at(15.4989, 73.8313, "18:10"), [MARKET]))  # too far

    def test_rain_starting_fires_only_on_the_change(self):
        dry, wet = at(15.55, 73.78, "13:00"), at(15.55, 73.78, "13:10", "rain")
        self.assertTrue(triggers.rain_starting(wet, dry))
        self.assertFalse(triggers.rain_starting(wet, wet))
        self.assertFalse(triggers.rain_starting(wet, None))

    def test_fired_names_the_trigger_or_stays_quiet(self):
        self.assertEqual(triggers.fired(at(15.4989, 73.8313, "09:00"), None, [FONTAINHAS]), "near_saved_spot")
        self.assertIsNone(triggers.fired(at(15.453, 73.804, "10:30"), None, [FONTAINHAS, MARKET]))


if __name__ == "__main__":
    unittest.main()
