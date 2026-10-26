import unittest
from tripplanner.geo import haversine_km, kmeans, road_km, travel_minutes


class GeoTests(unittest.TestCase):
    def test_zero_distance(self):
        self.assertAlmostEqual(haversine_km(26.9, 75.8, 26.9, 75.8), 0.0)

    def test_known_distance_amber_to_city_palace(self):
        d = haversine_km(26.9855, 75.8513, 26.9258, 75.8237)
        self.assertTrue(6.0 < d < 7.5, d)

    def test_symmetry(self):
        a, b = (26.92, 75.82), (26.98, 75.85)
        self.assertAlmostEqual(haversine_km(*a, *b), haversine_km(*b, *a))

    def test_road_distance_longer_than_straight_line(self):
        self.assertGreater(road_km(26.9, 75.8, 26.95, 75.85), haversine_km(26.9, 75.8, 26.95, 75.85))

    def test_travel_minutes_rounds_up_to_five(self):
        self.assertEqual(travel_minutes(0), 0)
        self.assertEqual(travel_minutes(1.0) % 5, 0)

    def test_kmeans_separates_two_obvious_groups(self):
        pts = [(26.99, 75.85), (26.98, 75.84), (26.99, 75.86), (26.80, 75.80), (26.81, 75.79), (26.80, 75.81)]
        labels = kmeans(pts, 2)
        self.assertEqual(len(set(labels[:3])), 1)
        self.assertEqual(len(set(labels[3:])), 1)
        self.assertNotEqual(labels[0], labels[3])

    def test_kmeans_deterministic_for_same_seed(self):
        pts = [(26.9 + i * 0.01, 75.8 + (i % 3) * 0.01) for i in range(12)]
        self.assertEqual(kmeans(pts, 3, seed=1), kmeans(pts, 3, seed=1))

    def test_kmeans_k_larger_than_points(self):
        self.assertEqual(kmeans([(1, 1), (2, 2)], 5), [0, 1])

    def test_kmeans_invalid_k(self):
        with self.assertRaises(ValueError):
            kmeans([(1, 1)], 0)


if __name__ == "__main__":
    unittest.main()
