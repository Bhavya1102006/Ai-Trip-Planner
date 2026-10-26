import unittest
from tripplanner.errors import InvalidBriefError
from tripplanner.parser import parse_brief


class ParserTests(unittest.TestCase):
    def test_full_brief(self):
        b = parse_brief("3 days in Jaipur, budget 3000 per day, interested in heritage and food, relaxed pace")
        self.assertEqual((b.destination, b.days, b.budget_per_day, b.pace), ("jaipur", 3, 3000, "relaxed"))
        self.assertEqual(set(b.interests), {"heritage", "food"})

    def test_total_budget_converted_to_per_day(self):
        b = parse_brief("2 days in Jaipur, total budget 8000, shopping")
        self.assertEqual(b.budget_per_day, 4000)

    def test_default_interests_when_none_given(self):
        b = parse_brief("2 days in Jaipur with budget 2500")
        self.assertEqual(b.interests, ["heritage", "food"])

    def test_default_interests_log_a_warning(self):
        with self.assertLogs("tripplanner", level="WARNING"):
            parse_brief("2 days in Jaipur, budget 2000, interested in underwater diving")

    def test_empty_brief_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("   ")

    def test_non_string_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief(None)

    def test_too_many_days_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("30 days in Jaipur, budget 3000")

    def test_zero_days_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("0 days in Jaipur, budget 3000")

    def test_budget_too_low_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("2 days in Jaipur, budget 100")

    def test_missing_budget_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("2 days in Jaipur, love food")

    def test_missing_days_rejected(self):
        with self.assertRaises(InvalidBriefError):
            parse_brief("a trip to Jaipur, budget 3000")


if __name__ == "__main__":
    unittest.main()
