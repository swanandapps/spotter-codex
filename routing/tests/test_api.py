from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from routing.models import FuelStation, Place
from routing.services.osrm import Route, RoutingError
from routing.services.planner import StationIndex

# A straight 1,200-mile "interstate" along latitude 40 from Kansas-ish to Ohio-ish.
ROUTE = Route(
    distance_miles=1200.0,
    duration_seconds=1200 / 60 * 3600,
    coordinates=[(40.0, -100.0), (40.0, -77.4)],
)


def station(name, lon, price, lat=40.0):
    return FuelStation(
        opis_id=abs(hash(name)) % 10**6, name=name, address="I-70", city=name, state="KS",
        rack_id=1, price=Decimal(str(price)), lat=lat, lon=lon,
    )


class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        StationIndex.reset()
        Place.objects.create(name="Startville", normalized="startville", state="KS", lat=40.0, lon=-100.0)
        Place.objects.create(name="Endtown", normalized="endtown", state="OH", lat=40.0, lon=-77.4)
        FuelStation.objects.bulk_create(
            [
                station("Mile150", -97.2, 3.50),
                station("Mile400", -92.5, 2.90),
                station("Mile420", -92.1, 3.90),
                station("Mile800", -84.9, 3.10),
                station("Offroute", -90.0, 1.00, lat=41.5),  # 100 miles north: ignored
            ]
        )

    def tearDown(self):
        StationIndex.reset()

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_plan_returns_stops_cost_and_map(self, fetch):
        resp = self.client.post(
            "/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(body["meta"]["routing_api_calls"], 1)
        names = [s["station"]["name"] for s in body["fuel_stops"]]
        self.assertNotIn("Offroute", names)
        self.assertIn("Mile400", names)  # the cheap one on route
        self.assertGreater(body["summary"]["total_fuel_cost"], 0)
        self.assertAlmostEqual(
            body["summary"]["total_fuel_cost"], sum(s["cost"] for s in body["fuel_stops"]), places=1
        )
        # Full 50-gal tank, 5-gal reserve never planned for use: buys (1200 - 450) mi of fuel.
        self.assertAlmostEqual(body["summary"]["total_gallons_purchased"], (1200 - 450) / 10, places=1)
        self.assertAlmostEqual(body["summary"]["fuel_at_arrival_gallons"], 5.0, places=1)
        for stop in body["fuel_stops"]:
            self.assertGreaterEqual(stop["fuel_on_arrival_gallons"], 5.0 - 1e-6)
            self.assertTrue(stop["reason"])
            self.assertIn("google.com/maps", stop["navigation_url"])
        self.assertIn("just enough to reach Endtown", body["fuel_stops"][-1]["reason"])
        self.assertGreaterEqual(body["savings"]["saved_usd"], 0)
        kinds = [f["properties"]["kind"] for f in body["map"]["features"]]
        self.assertEqual(kinds[:3], ["route", "start", "finish"])
        self.assertEqual(kinds.count("fuel_stop"), len(body["fuel_stops"]))
        self.assertIn("/api/route/map/?", body["map_url"])

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_repeat_request_is_served_from_cache(self, fetch):
        url = "/api/route/?start=Startville, KS&finish=Endtown, OH"
        self.client.get(url)
        body = self.client.get(url).json()
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(body["meta"]["cache_hit"])
        self.assertEqual(body["meta"]["routing_api_calls"], 0)

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_map_page_renders(self, fetch):
        resp = self.client.get("/api/route/map/", {"start": "Startville, KS", "finish": "Endtown, OH"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "plan-data")
        self.assertContains(resp, "maplibre-gl")

    def test_border_towns_across_the_line_are_rejected(self):
        for point in ("43.6532,-79.3832", "32.5149,-117.0382", "42.3149,-83.0364"):  # Toronto, Tijuana, Windsor ON
            resp = self.client.get("/api/route/", {"start": point, "finish": "Endtown, OH"})
            self.assertEqual(resp.status_code, 400, point)
            self.assertIn("outside the USA", resp.json()["error"])

    @mock.patch("routing.services.osrm.fetch_routes")
    def test_comparison_text_uses_requested_stop_penalty(self, fetch):
        fetch.return_value = [ROUTE]
        body = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH", "stop_penalty": 0}).json()
        self.assertIn("$0/stop", body["route_options"]["compared_on"])

    def test_coordinates_outside_usa_rejected(self):
        resp = self.client.get("/api/route/", {"start": "51.5,-0.12", "finish": "Endtown, OH"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("outside the USA", resp.json()["error"])

    def test_missing_fields_rejected(self):
        resp = self.client.post("/api/route/", {"start": "Startville, KS"}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json())

    @mock.patch("routing.services.osrm.fetch_routes", side_effect=RoutingError("no route"))
    def test_routing_failure_is_502(self, fetch):
        resp = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH"})
        self.assertEqual(resp.status_code, 502)

    @mock.patch("routing.services.osrm.fetch_routes")
    def test_unreachable_gap_is_422(self, fetch):
        fetch.return_value = [Route(2000.0, 0, [(40.0, -100.0), (40.0, -62.0)])]
        resp = self.client.get("/api/route/", {"start": "40,-100", "finish": "40,-80"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("No fuel station is within reach", resp.json()["error"])
        self.assertIn("reserve", resp.json()["error"])

    def test_non_us_state_code_rejected_without_lookup(self):
        with mock.patch("routing.services.geocode.requests.get") as get:
            resp = self.client.get("/api/route/", {"start": "Toronto, ON", "finish": "Endtown, OH"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not a US state", resp.json()["error"])
        get.assert_not_called()

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_gazetteer_inputs_make_no_geocoding_calls(self, fetch):
        with mock.patch("routing.services.geocode.requests.get") as get:
            body = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH"}).json()
        get.assert_not_called()
        self.assertEqual(body["meta"]["geocoding_api_calls"], 0)

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_starting_fuel_from_eld_changes_what_is_bought(self, fetch):
        body = self.client.post(
            "/api/route/",
            {"start": "Startville, KS", "finish": "Endtown, OH", "start_fuel_gallons": 20},
            content_type="application/json",
        ).json()
        # 20 gal = 200 mi, 150 usable above the reserve: buys the other 1,050 mi.
        self.assertAlmostEqual(body["summary"]["total_gallons_purchased"], 105.0, places=1)
        self.assertEqual(body["vehicle"]["starting_fuel_gallons"], 20)
        self.assertIn("start_fuel_gallons=20", body["map_url"])

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[Route(0.0, 0, [(40.0, -100.0), (40.0, -100.0)])])
    def test_same_start_and_finish_is_400(self, fetch):
        resp = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "40,-100"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("same place", resp.json()["error"])

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_low_starting_fuel_gap_message_names_the_real_limit(self, fetch):
        # 6 gal = 60 mi, 10 usable above the reserve; first station is ~148 mi out.
        resp = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH", "start_fuel_gallons": 6})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("more than the 10 miles", resp.json()["error"])

    def test_geocoding_service_failure_is_502(self):
        import requests

        with mock.patch("routing.services.geocode.requests.get", side_effect=requests.ConnectionError("down")):
            resp = self.client.get("/api/route/", {"start": "123 Main Street Springfield", "finish": "Endtown, OH"})
        self.assertEqual(resp.status_code, 502)

    def test_gazetteer_labels_drop_census_suffixes(self):
        from routing.services import geocode
        from routing.services.geo import normalize_place

        for name in ("Juneau city and borough", "Indianapolis city (balance)"):
            Place.objects.create(name=name, normalized=normalize_place(name), state="ZZ", lat=40.0, lon=-90.0)
        self.assertEqual(geocode._from_gazetteer("Juneau", "ZZ").label, "Juneau, ZZ")
        self.assertEqual(geocode._from_gazetteer("Indianapolis", "ZZ").label, "Indianapolis, ZZ")

    @mock.patch("routing.services.osrm.fetch_routes")
    def test_cheaper_alternate_route_is_chosen(self, fetch):
        # OSRM's fastest route runs 140 mi north, past only expensive stations.
        detour = Route(1205.0, ROUTE.duration_seconds + 60, [(40.0, -100.0), (40.0, -77.4)])
        pricey = Route(1200.0, ROUTE.duration_seconds, [(42.0, -100.0), (42.0, -77.4)])
        FuelStation.objects.bulk_create([
            station("North1", -95.0, 4.50, lat=42.0), station("North2", -88.0, 4.60, lat=42.0),
            station("North3", -82.0, 4.70, lat=42.0),
        ])
        fetch.return_value = [pricey, detour]
        body = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH"}).json()
        options = body["route_options"]["options"]
        self.assertEqual(body["meta"]["routes_compared"], 2)
        self.assertEqual(body["meta"]["routing_api_calls"], 1)
        self.assertTrue(options[0]["chosen"])
        self.assertEqual(options[0]["option"], 1)  # the alternate, not OSRM's fastest
        self.assertGreater(options[1]["fuel_cost_vs_chosen"], 0)
        self.assertIn("alternate_route", [f["properties"]["kind"] for f in body["map"]["features"]])

    @mock.patch("routing.services.osrm.fetch_routes")
    def test_slow_alternate_loses_even_if_fuel_is_slightly_cheaper(self, fetch):
        # Alternate saves a little fuel but adds 3 hours of driving ($90 at $30/h).
        slow = Route(1200.0, ROUTE.duration_seconds + 3 * 3600, [(40.0, -100.0), (40.0, -77.4)])
        FuelStation.objects.filter(name="Mile400").update(price=Decimal("2.89"))
        fetch.return_value = [ROUTE, slow]
        body = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH"}).json()
        self.assertEqual(body["route_options"]["options"][0]["option"], 0)

    def test_starting_below_reserve_is_422(self):
        resp = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH", "start_fuel_gallons": 3})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("Fuel up before departing", resp.json()["error"])

    def test_starting_fuel_above_tank_rejected(self):
        resp = self.client.get("/api/route/", {"start": "Startville, KS", "finish": "Endtown, OH", "start_fuel_gallons": 80})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("start_fuel_gallons", resp.json())

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_price_reload_invalidates_cached_plans(self, fetch):
        url = "/api/route/?start=Startville, KS&finish=Endtown, OH"
        before = self.client.get(url).json()
        FuelStation.objects.filter(name="Mile400").update(price=Decimal("2.10"))
        after = self.client.get(url).json()
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(after["meta"]["cache_hit"])
        self.assertLess(after["summary"]["total_fuel_cost"], before["summary"]["total_fuel_cost"])

    @mock.patch("routing.services.osrm.fetch_routes", return_value=[ROUTE])
    def test_map_page_explains_bad_fuel_value(self, fetch):
        resp = self.client.get("/api/route/map/", {"start": "Startville, KS", "finish": "Endtown, OH", "start_fuel_gallons": "90"})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "between 0 and 50 gallons", status_code=400)

    def test_empty_map_page_shows_trip_form(self):
        resp = self.client.get("/api/route/map/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="start_fuel_gallons"')


class OsrmRetryTests(TestCase):
    def _ok(self):
        resp = mock.Mock(status_code=200, headers={"content-type": "application/json"})
        resp.json.return_value = {
            "code": "Ok",
            "routes": [{"distance": 1609.344, "duration": 60, "geometry": {"coordinates": [[-100, 40], [-99, 40]]}}],
        }
        return resp

    @mock.patch("routing.services.osrm.time.sleep")
    def test_one_retry_on_timeout(self, _sleep):
        import requests

        from routing.services import osrm

        with mock.patch("routing.services.osrm.requests.get", side_effect=[requests.Timeout(), self._ok()]) as get:
            route = osrm.fetch_routes((40, -100), (40, -99))[0]
        self.assertEqual(get.call_count, 2)
        self.assertEqual(route.api_calls, 2)

    @mock.patch("routing.services.osrm.time.sleep")
    def test_one_retry_on_server_error(self, _sleep):
        from routing.services import osrm

        busy = mock.Mock(status_code=503, headers={"content-type": "text/html"})
        with mock.patch("routing.services.osrm.requests.get", side_effect=[busy, self._ok()]) as get:
            route = osrm.fetch_routes((40, -100), (40, -99))[0]
        self.assertEqual(get.call_count, 2)
        self.assertEqual(route.api_calls, 2)

    def test_no_route_is_the_callers_problem(self):
        from routing.services import osrm

        resp = mock.Mock(status_code=400, headers={"content-type": "application/json"})
        resp.json.return_value = {"code": "NoRoute", "message": "Impossible route between points"}
        with mock.patch("routing.services.osrm.requests.get", return_value=resp):
            with self.assertRaises(osrm.RoutingError) as ctx:
                osrm.fetch_routes((47.6, -122.3), (21.3, -157.8))
        self.assertEqual(ctx.exception.status, 422)

    @mock.patch("routing.services.osrm.time.sleep")
    def test_no_retry_on_client_error(self, _sleep):
        from routing.services import osrm

        bad = mock.Mock(status_code=400, headers={"content-type": "application/json"})
        bad.json.return_value = {"code": "InvalidQuery", "message": "Invalid coordinates"}
        with mock.patch("routing.services.osrm.requests.get", return_value=bad) as get:
            with self.assertRaises(osrm.RoutingError):
                osrm.fetch_routes((40, -100), (40, -99))
        self.assertEqual(get.call_count, 1)


class PlacesApiTests(TestCase):
    def setUp(self):
        from routing.services.geo import normalize_place

        for name, state, pop, lat, lon in [
            ("Chicago city", "IL", 2_664_452, 41.84, -87.68),
            ("Chico city", "CA", 101_475, 39.75, -121.83),
            ("Chicago Heights city", "IL", 26_929, 41.51, -87.64),
            ("Preston Heights CDP", "IL", 0, 41.49, -88.08),
            ("Joliet city", "IL", 150_371, 41.52, -88.12),
        ]:
            Place.objects.create(name=name, normalized=normalize_place(name), state=state,
                                 population=pop, lat=lat, lon=lon)

    def test_autocomplete_ranks_by_population(self):
        labels = [p["label"] for p in self.client.get("/api/places/", {"q": "ch"}).json()]
        self.assertEqual(labels[:3], ["Chicago, IL", "Chico, CA", "Chicago Heights, IL"])

    def test_autocomplete_filters_by_state(self):
        labels = [p["label"] for p in self.client.get("/api/places/", {"q": "chic, ca"}).json()]
        self.assertEqual(labels, ["Chico, CA"])

    def test_autocomplete_short_or_empty_query(self):
        self.assertEqual(self.client.get("/api/places/", {"q": ""}).json(), [])

    def test_dropped_pin_is_named_after_the_town_drivers_know(self):
        from routing.services.places import _PlaceIndex

        _PlaceIndex._arrays = None
        body = self.client.get("/api/places/nearest/", {"lat": 41.49, "lon": -88.085}).json()
        self.assertEqual(body["label"], "Joliet, IL")

    def test_nearest_needs_numbers(self):
        self.assertEqual(self.client.get("/api/places/nearest/", {"lat": "x"}).status_code, 400)

    def test_nearest_rejects_nan_and_points_outside_usa(self):
        for lat, lon in (("nan", "-90"), ("100", "-90"), ("43.65", "-79.38")):
            self.assertEqual(self.client.get("/api/places/nearest/", {"lat": lat, "lon": lon}).status_code, 400)
