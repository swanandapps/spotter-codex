import numpy as np
from django.test import SimpleTestCase

from routing.services.corridor import stations_along_route
from routing.services.geo import densify, haversine_miles, normalize_place, place_aliases, thin


class NormalizePlaceTests(SimpleTestCase):
    def test_matches_census_names_to_station_csv_names(self):
        pairs = [
            ("Big Cabin town", "Big Cabin"),
            ("East St. Louis city", "East Saint Louis"),
            ("McCalla CDP", "Mc Calla"),
            ("DeForest village", "De Forest"),
            ("Mount Nebo CDP", "Mt Nebo"),
        ]
        for census, station in pairs:
            self.assertEqual(normalize_place(census), normalize_place(station), (census, station))

    def test_consolidated_city_alias(self):
        keys = place_aliases("Nashville-Davidson metropolitan government (balance)")
        self.assertIn(normalize_place("Nashville"), keys)


class GeometryTests(SimpleTestCase):
    def test_densify_preserves_length_and_bounds_step(self):
        coords = [(40.0, -100.0), (40.0, -95.0)]  # ~263 miles
        dense, cum = densify(coords, max_step_miles=1.0)
        self.assertAlmostEqual(cum[-1], haversine_miles(40.0, -100.0, 40.0, -95.0), delta=0.5)
        self.assertLessEqual(np.diff(cum).max(), 1.0 + 1e-9)
        np.testing.assert_allclose(dense[-1], coords[-1])

    def test_thin_keeps_endpoints(self):
        coords, _ = densify([(40.0, -100.0), (40.0, -99.0)], max_step_miles=0.05)
        out = thin(coords, 0.5)
        self.assertLess(len(out), len(coords) / 5)
        np.testing.assert_allclose(out[0], coords[0])
        np.testing.assert_allclose(out[-1], coords[-1])

    def test_corridor_finds_nearby_stations_with_route_mileage(self):
        route = [(40.0, -100.0), (40.0, -95.0)]
        lat = np.array([40.05, 40.5, 40.0])  # ~3.5 mi off, ~35 mi off, on the line
        lon = np.array([-97.5, -97.5, -95.0])
        hits = {h.index: h for h in stations_along_route(route, lat, lon, corridor_miles=10)}
        self.assertEqual(set(hits), {0, 2})
        self.assertAlmostEqual(hits[0].offset_miles, 3.45, delta=0.2)
        self.assertAlmostEqual(hits[0].route_mile, haversine_miles(40, -100, 40, -97.5), delta=1)
        self.assertAlmostEqual(hits[2].route_mile, haversine_miles(40, -100, 40, -95), delta=1)
