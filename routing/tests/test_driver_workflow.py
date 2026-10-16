from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

import numpy as np
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from routing.models import FuelStation
from routing.services import geocode, osrm, route_cache
from routing.services.corridor import stations_along_route
from routing.services.osrm import Route, RoutingError
from routing.tests import test_api as fixtures


class DriverWorkflowTests(TestCase):
    setUp = fixtures.RouteApiTests.setUp
    tearDown = fixtures.RouteApiTests.tearDown

    def request_plan(self, **kwargs):
        return self.client.get('/api/route/', {'start': 'Startville, KS', 'finish': 'Endtown, OH', **kwargs})

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_avoiding_a_station_replans_locally_and_round_trips_through_map_url(self, fetch):
        original = self.request_plan().json()
        avoided = original['fuel_stops'][0]['station']['id']
        other = FuelStation.objects.get(name='Offroute').pk
        response = self.client.post('/api/route/', {
            'start': 'Startville, KS', 'finish': 'Endtown, OH', 'avoid_station_ids': [avoided, other],
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        updated = response.json()
        self.assertNotIn(avoided, [s['station']['id'] for s in updated['fuel_stops']])
        self.assertEqual({s['id'] for s in updated['excluded_stations']}, {avoided, other})
        self.assertTrue(updated['meta']['road_cache_hit'])
        self.assertFalse(updated['meta']['cache_hit'])
        self.assertEqual(updated['meta']['routing_api_calls'], 0)
        url = urlsplit(updated['map_url'])
        page = self.client.get(url.path + '?' + url.query)
        self.assertEqual(page.status_code, 200, page.content)
        self.assertEqual(len(page.context['plan']['excluded_stations']), 2)
        self.assertEqual(fetch.call_count, 1)
        restored = self.request_plan().json()
        self.assertEqual(restored['fuel_stops'], original['fuel_stops'])

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_exclusions_that_remove_all_reachable_stations_do_not_poison_a_valid_plan(self, fetch):
        original = self.request_plan().json()
        failed = self.request_plan(avoid_station_ids=list(FuelStation.objects.values_list('id', flat=True)))
        self.assertEqual(failed.status_code, 422)
        self.assertIn('excluded', failed.json()['error'])
        self.assertEqual(self.request_plan().json()['summary'], original['summary'])
        self.assertEqual(fetch.call_count, 1)

    def test_exclusion_ids_are_bounded_and_must_exist(self):
        for values in (['bad'], [-1], list(range(1, 22)), [9999999]):
            with self.subTest(values=values), mock.patch('routing.services.osrm.fetch_routes') as fetch:
                self.assertEqual(self.request_plan(avoid_station_ids=values).status_code, 400)
                fetch.assert_not_called()

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_leg_totals_match_trip_and_fuel_inventory(self, fetch):
        result = self.request_plan().json()
        self.assertEqual(len(result['legs']), len(result['fuel_stops']) + 1)
        self.assertAlmostEqual(sum(l['distance_miles'] for l in result['legs']), result['route']['distance_miles'], delta=.2)
        self.assertAlmostEqual(sum(l['driving_hours'] for l in result['legs']), result['route']['duration_hours'], delta=.03)
        for leg in result['legs']:
            self.assertGreaterEqual(leg['arrival_fuel_gallons'], result['vehicle']['reserve_gallons'] - .01)
            self.assertLessEqual(leg['distance_miles'], 450.1)
            self.assertAlmostEqual(leg['departure_fuel_gallons'] - leg['arrival_fuel_gallons'], leg['distance_miles'] / 10, delta=.02)
        self.assertIsNone(result['legs'][-1]['to_stop_number'])
        self.assertEqual(round(sum(s['cost'] for s in result['fuel_stops']), 2), result['summary']['total_fuel_cost'])
        self.assertEqual(round(result['savings']['habit_fuel_cost'] - result['summary']['total_fuel_cost'], 2), result['savings']['saved_usd'])

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_starting_fuel_and_cost_changes_reuse_road_geometry(self, fetch):
        first = self.request_plan().json()
        changed = self.request_plan(start_fuel_gallons=20, stop_penalty=0, driver_hourly_cost=0).json()
        self.assertFalse(changed['meta']['cache_hit'])
        self.assertTrue(changed['meta']['road_cache_hit'])
        self.assertEqual(changed['meta']['routing_api_calls'], 0)
        self.assertGreater(changed['summary']['total_gallons_purchased'], first['summary']['total_gallons_purchased'])
        self.assertEqual(fetch.call_count, 1)

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_brief_is_self_contained_and_uses_same_plan_without_another_route_request(self, fetch):
        body = self.request_plan().json()
        params = {'start': 'Startville, KS', 'finish': 'Endtown, OH'}
        preview = self.client.get('/api/route/brief/', params)
        self.assertContains(preview, 'Print / save PDF')
        self.assertContains(preview, str(body['summary']['total_fuel_cost']))
        self.assertContains(preview, 'Mile400')
        self.assertNotContains(preview, '<script src=')
        self.assertNotContains(preview, '<link ')
        self.assertNotContains(preview, '<img ')
        download = self.client.get('/api/route/brief/', {**params, 'download': '1'})
        self.assertEqual(download['Content-Disposition'], 'attachment; filename="spotter-trip-brief.html"')
        self.assertNotContains(download, 'Save offline copy')
        self.assertEqual(fetch.call_count, 1)

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_brief_escapes_untrusted_station_text(self, fetch):
        FuelStation.objects.filter(name='Mile400').update(name='<script>alert(1)</script>')
        response = self.client.get('/api/route/brief/', {'start': 'Startville, KS', 'finish': 'Endtown, OH'})
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    @mock.patch('routing.services.geocode.requests.get')
    def test_address_geocoding_is_reused_and_bad_response_is_not_cached(self, get):
        get.return_value.json.return_value = [{'lat': '40', 'lon': '-100', 'display_name': 'A US address'}]
        first = geocode.resolve('123 Example St')
        again = geocode.resolve('123 Example St')
        self.assertEqual(first.source, 'nominatim')
        self.assertEqual(again.source, 'nominatim_cache')
        self.assertEqual(get.call_count, 1)
        get.return_value.json.return_value = {'unexpected': True}
        with self.assertRaises(geocode.GeocodingServiceError):
            geocode.resolve('456 Example St')
        get.return_value.json.return_value = [{'lat': '40', 'lon': '-100'}]
        self.assertEqual(geocode.resolve('456 Example St').point, (40, -100))
        self.assertEqual(get.call_count, 3)


class RoadEstimateTests(SimpleTestCase):
    def test_total_sums_rounded_purchases_instead_of_rounding_the_unpaid_total(self):
        from routing.services.optimizer import Candidate, FuelPlan, Stop
        from routing.services.planner import _purchase_total
        plan = FuelPlan([Stop(Candidate(10, 1), 1.005, 1.005, 0), Stop(Candidate(20, 1), 1.005, 1.005, 0)], 2.01, 2.01)
        self.assertEqual(_purchase_total(plan), 2.02)

    def test_segment_speeds_determine_times_and_totals_are_preserved(self):
        route = Route(200, 4 * 3600, [(40, -100), (40, -98), (40, -96)], 1, (100, 100), (3600, 7200))
        self.assertAlmostEqual(route.time_at_mile(100), 4 * 3600 / 3)
        self.assertAlmostEqual(route.time_at_mile(150), 4 * 3600 * 2 / 3)
        self.assertEqual(route.time_at_mile(200), route.duration_seconds)
        self.assertEqual(route.time_at_mile(1000), route.duration_seconds)
        self.assertEqual(route.time_at_mile(-1), 0)

    def test_station_mileage_uses_road_distances_instead_of_geometry_length(self):
        hits = stations_along_route([(40, -100), (40, -98), (40, -96)],
                                    np.array([40., 40.]), np.array([-98., -96.]), 10,
                                    route_distance_miles=400, segment_distances_miles=(100, 300))
        self.assertAlmostEqual(hits[0].route_mile, 100, delta=.01)
        self.assertAlmostEqual(hits[1].route_mile, 400, delta=.01)

    @mock.patch('routing.services.osrm.requests.get')
    def test_malformed_router_payload_is_a_friendly_service_error(self, get):
        response = get.return_value
        response.status_code = 200
        response.headers = {'content-type': 'application/json'}
        for data in ([1], {'code': 'Ok', 'routes': [{'distance': 'nan'}]},
                     {'code': 'Ok', 'routes': [{'distance': 100, 'duration': 10, 'geometry': {'coordinates': [[-100, 40], [-99, 40]]}, 'legs': [None]}]}):
            with self.subTest(data=data):
                response.json.return_value = data
                with self.assertRaises(RoutingError) as failure:
                    osrm.fetch_routes((40, -100), (40, -99))
                self.assertEqual(failure.exception.status, 502)


class SharedRoadCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch('routing.services.osrm.fetch_routes', side_effect=[RoutingError('temporarily unavailable'), [fixtures.ROUTE]])
    def test_router_failures_are_not_cached(self, fetch):
        with self.assertRaises(RoutingError):
            route_cache.get_routes((40, -100), (40, -80))
        routes, reused = route_cache.get_routes((40, -100), (40, -80))
        self.assertFalse(reused)
        self.assertEqual(routes[0].api_calls, 1)
        self.assertEqual(fetch.call_count, 2)

    def test_simultaneous_identical_requests_share_one_external_call(self):
        first_entered, second_read, release = Event(), Event(), Event()
        get_original = cache.get
        reads, lock = 0, Lock()

        def read(*args, **kwargs):
            nonlocal reads
            with lock:
                reads += 1
                if reads == 2:
                    second_read.set()
            return get_original(*args, **kwargs)

        def fetch(*args):
            first_entered.set()
            release.wait(3)
            return [fixtures.ROUTE]

        with mock.patch('routing.services.route_cache.cache', SimpleNamespace(get=read, set=cache.set)), mock.patch('routing.services.osrm.fetch_routes', side_effect=fetch) as call:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(route_cache.get_routes, (40, -100), (40, -80))
                self.assertTrue(first_entered.wait(2))
                second = pool.submit(route_cache.get_routes, (40, -100), (40, -80))
                self.assertTrue(second_read.wait(2))
                release.set()
                a, b = first.result(3), second.result(3)
        self.assertEqual(call.call_count, 1)
        self.assertEqual((a[0][0].api_calls, b[0][0].api_calls), (1, 0))
        self.assertEqual((a[1], b[1]), (False, True))


class ImportStabilityTests(TestCase):
    def test_price_reload_preserves_station_ids_and_removes_retired_rows(self):
        import csv
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from io import StringIO
        current = FuelStation.objects.create(opis_id=123, name='Old name', address='Exit 1', city='Town', state='KS', price=3, lat=40, lon=-100)
        retired = FuelStation.objects.create(opis_id=456, name='Retired', address='Exit 2', city='Town', state='KS', price=4, lat=40, lon=-100)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            fields = ['opis_id', 'name', 'address', 'city', 'state', 'rack_id', 'price', 'lat', 'lon']
            with (folder / 'fuel_stations_geocoded.csv').open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
                writer.writerow(dict(zip(fields, [123, 'Updated name', 'Exit 1', 'Town', 'KS', '', '2.999', 40, -100])))
            with mock.patch('routing.management.commands.load_data.DATA', folder), mock.patch('routing.management.commands.load_data.load_place_index', return_value={}):
                call_command('load_data', stdout=StringIO())
                call_command('load_data', stdout=StringIO())
        current.refresh_from_db()
        self.assertEqual(current.name, 'Updated name')
        self.assertEqual(float(current.price), 2.999)
        self.assertEqual(FuelStation.objects.get(opis_id=123).pk, current.pk)
        self.assertFalse(FuelStation.objects.filter(pk=retired.pk).exists())


class ProviderPacingTests(SimpleTestCase):
    def test_geocoding_misses_are_spaced_at_least_one_second_apart(self):
        with mock.patch.object(geocode, '_last_request_at', 10.0), mock.patch('routing.services.geocode.time.monotonic', side_effect=[10.2, 11.0]), mock.patch('routing.services.geocode.time.sleep') as sleep:
            geocode._wait_for_request_slot()
        self.assertAlmostEqual(sleep.call_args.args[0], .8)
