import json
import os
import tempfile
import unittest
from unittest import mock

from tripplanner import data_loader
from tripplanner.data_loader import load_pois
from tripplanner.errors import InfeasiblePlanError, PlannerError, UnknownDestinationError
from tripplanner.models import POI, TripBrief
from tripplanner.planner import (DAY_END, STOPS_PER_DAY, baseline_km, plan_trip, score_poi,
                                 select_candidates, sequence_day)

POIS = load_pois("jaipur")


def brief(**kw):
    base = dict(destination="jaipur", days=3, budget_per_day=3000,
                interests=["heritage", "food"], pace="moderate")
    base.update(kw)
    return TripBrief(**base)


class DataTests(unittest.TestCase):
    def test_loads_all_pois(self):
        self.assertEqual(len(POIS), 24)

    def test_unknown_destination(self):
        with self.assertRaises(UnknownDestinationError):
            load_pois("atlantis")

    def test_corrupt_file_raises_planner_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.json"), "w") as fh:
                fh.write("{not json")
            with mock.patch.object(data_loader, "DATA_DIR", tmp), \
                 mock.patch.dict(data_loader.AVAILABLE, {"testcity": "x.json"}):
                with self.assertRaises(PlannerError):
                    load_pois("testcity")

    def test_bad_rows_are_skipped(self):
        rows = [{"id": "a", "name": "A", "lat": 95.0, "lon": 75, "tags": ["food"], "open": "09:00",
                 "close": "17:00", "duration": 30, "cost": 0, "rating": 4, "mainstream": True},
                {"id": "b", "name": "B", "lat": 26.9, "lon": 75, "tags": ["food"], "open": "18:00",
                 "close": "09:00", "duration": 30, "cost": 0, "rating": 4, "mainstream": True}]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "y.json"), "w") as fh:
                json.dump(rows, fh)
            with mock.patch.object(data_loader, "DATA_DIR", tmp), \
                 mock.patch.dict(data_loader.AVAILABLE, {"testcity": "y.json"}):
                self.assertEqual(load_pois("testcity"), [])


class ScoringTests(unittest.TestCase):
    def test_no_overlap_scores_zero(self):
        p = next(p for p in POIS if p.id == "raj_mandir")
        self.assertEqual(score_poi(p, ["nature"]), 0.0)

    def test_more_overlap_scores_higher(self):
        amber = next(p for p in POIS if p.id == "amber_fort")
        self.assertGreater(score_poi(amber, ["heritage", "photography"]), score_poi(amber, ["heritage"]))

    def test_non_mainstream_bonus(self):
        a = POI("a", "A", 26.9, 75.8, ("food",), 0, 1000, 30, 0, 4.0, True)
        b = POI("b", "B", 26.9, 75.8, ("food",), 0, 1000, 30, 0, 4.0, False)
        self.assertGreater(score_poi(b, ["food"]), score_poi(a, ["food"]))

    def test_no_matching_interest_is_infeasible(self):
        with self.assertRaises(InfeasiblePlanError):
            select_candidates(brief(interests=["underwater"]), POIS)


class SequencingTests(unittest.TestCase):
    def setUp(self):
        self.it = plan_trip(brief(), POIS)

    def test_one_day_plan_per_requested_day(self):
        self.assertEqual(len(self.it.days), 3)

    def test_every_stop_inside_opening_hours(self):
        for d in self.it.days:
            for s in d.stops:
                self.assertGreaterEqual(s.arrive, s.poi.open_min)
                self.assertLessEqual(s.depart, s.poi.close_min)

    def test_no_day_runs_past_closing_time_of_day(self):
        for d in self.it.days:
            for s in d.stops:
                self.assertLessEqual(s.depart, DAY_END)

    def test_stops_do_not_overlap_in_time(self):
        for d in self.it.days:
            for a, b in zip(d.stops, d.stops[1:]):
                self.assertGreaterEqual(b.arrive, a.depart)

    def test_daily_budget_respected(self):
        for d in self.it.days:
            self.assertLessEqual(d.cost, 3000)

    def test_pace_cap_respected(self):
        for d in self.it.days:
            self.assertLessEqual(len(d.stops), STOPS_PER_DAY["moderate"])

    def test_no_place_visited_twice(self):
        ids = [s.poi.id for d in self.it.days for s in d.stops]
        self.assertEqual(len(ids), len(set(ids)))

    def test_deterministic(self):
        again = plan_trip(brief(), POIS)
        self.assertEqual([[s.poi.id for s in d.stops] for d in self.it.days],
                         [[s.poi.id for s in d.stops] for d in again.days])

    def test_tight_budget_limits_spend(self):
        it = plan_trip(brief(budget_per_day=600, days=2), POIS)
        for d in it.days:
            self.assertLessEqual(d.cost, 600)

    def test_zero_cost_places_still_plannable_on_minimum_budget(self):
        it = plan_trip(brief(budget_per_day=500, days=1, interests=["nature"]), POIS)
        self.assertGreater(it.stop_count, 0)

    def test_sequence_day_waits_for_opening_time(self):
        late = POI("l", "Late", 26.9, 75.8, ("food",), 12 * 60, 20 * 60, 30, 0, 4.0, True)
        stops, _ = sequence_day([late], 1000, "moderate")
        self.assertEqual(stops[0].arrive, 12 * 60)

    def test_unreachable_place_is_reported_not_crashed(self):
        closed = POI("c", "Closed", 26.9, 75.8, ("food",), 21 * 60, 22 * 60, 30, 0, 4.0, True)
        stops, dropped = sequence_day([closed], 1000, "moderate")
        self.assertEqual((stops, dropped), ([], ["Closed"]))

    def test_everything_unaffordable_raises(self):
        with self.assertRaises(InfeasiblePlanError):
            plan_trip(brief(budget_per_day=500, interests=["nightlife"], days=1,
                            pace="relaxed"), [p for p in POIS if p.id == "chokhi_dhani"])


class NightlifeTests(unittest.TestCase):
    def test_evening_venue_schedulable_when_nightlife_requested(self):
        it = plan_trip(brief(days=2, budget_per_day=3500, interests=["nightlife"], pace="packed"), POIS)
        names = [s.poi.id for d in it.days for s in d.stops]
        self.assertIn("chokhi_dhani", names)

    def test_evening_venue_not_scheduled_without_nightlife(self):
        it = plan_trip(brief(days=2, interests=["food"], pace="packed"), POIS)
        self.assertNotIn("chokhi_dhani", [s.poi.id for d in it.days for s in d.stops])


class EfficiencyTests(unittest.TestCase):
    def test_clustered_plan_beats_naive_baseline(self):
        b = brief(days=3, pace="moderate")
        self.assertLess(plan_trip(b, POIS).total_km, baseline_km(b, POIS))


if __name__ == "__main__":
    unittest.main()
