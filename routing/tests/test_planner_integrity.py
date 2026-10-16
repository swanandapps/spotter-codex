from decimal import Decimal
from unittest import mock

from django.test import TestCase

from routing.models import FuelStation
from routing.services.osrm import Route
from routing.tests import test_api as fixtures


class PlannerIntegrityTests(TestCase):
    setUp = fixtures.RouteApiTests.setUp
    tearDown = fixtures.RouteApiTests.tearDown

    def request_plan(self, **kwargs):
        return self.client.get('/api/route/', {
            'start': 'Startville, KS', 'finish': 'Endtown, OH', **kwargs,
        })

    def test_non_finite_costs_and_starting_fuel_are_rejected_before_routing(self):
        with mock.patch('routing.services.osrm.fetch_routes') as fetch:
            for field in ('start_fuel_gallons', 'stop_penalty', 'driver_hourly_cost'):
                for value in ('nan', 'inf', '-inf'):
                    with self.subTest(field=field, value=value):
                        response = self.request_plan(**{field: value})
                        self.assertEqual(response.status_code, 400)
                        self.assertIn(field, response.json())
            fetch.assert_not_called()

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_balanced_price_changes_invalidate_the_index_and_cached_plan(self, fetch):
        before = self.request_plan().json()
        # Same row count, max ID and total price; the old aggregate fingerprint missed this.
        FuelStation.objects.filter(name='Mile400').update(price=Decimal('3.90'))
        FuelStation.objects.filter(name='Mile420').update(price=Decimal('2.90'))
        after = self.request_plan().json()
        self.assertFalse(after['meta']['cache_hit'])
        self.assertEqual(fetch.call_count, 1)
        self.assertIn('Mile400', [s['station']['name'] for s in before['fuel_stops']])
        self.assertIn('Mile420', [s['station']['name'] for s in after['fuel_stops']])
        for stop in after['fuel_stops']:
            actual = FuelStation.objects.get(pk=stop['station']['id'])
            self.assertEqual(stop['station']['price_per_gallon'], float(actual.price))

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_updated_station_address_is_not_hidden_by_plan_cache(self, fetch):
        before = self.request_plan().json()
        selected = before['fuel_stops'][0]['station']['id']
        FuelStation.objects.filter(pk=selected).update(address='Corrected exit 12')
        after = self.request_plan().json()
        self.assertEqual(after['fuel_stops'][0]['station']['address'], 'Corrected exit 12')
        self.assertFalse(after['meta']['cache_hit'])

    @mock.patch('routing.services.osrm.fetch_routes')
    def test_fuel_only_can_choose_cheaper_slower_route(self, fetch):
        north = Route(1200.0, fixtures.ROUTE.duration_seconds, [(42., -100.), (42., -77.4)])
        slow = Route(1200.0, fixtures.ROUTE.duration_seconds + 3 * 3600, fixtures.ROUTE.coordinates)
        FuelStation.objects.bulk_create([
            fixtures.station('North1', -95., 3.40, lat=42.),
            fixtures.station('North2', -88., 3.40, lat=42.),
            fixtures.station('North3', -82., 3.40, lat=42.),
        ])
        fetch.return_value = [north, slow]
        balanced = self.request_plan(stop_penalty=0, driver_hourly_cost=30).json()
        fuel_only = self.request_plan(stop_penalty=0, driver_hourly_cost=0).json()
        self.assertEqual(balanced['route_options']['recommended'], 0)
        self.assertEqual(fuel_only['route_options']['recommended'], 1)
        self.assertLess(fuel_only['summary']['total_fuel_cost'], balanced['summary']['total_fuel_cost'])
        self.assertIn('$0/hour', fuel_only['route_options']['compared_on'])
        again = self.request_plan(stop_penalty=0, driver_hourly_cost=0).json()
        self.assertTrue(again['meta']['cache_hit'])
        self.assertEqual(fetch.call_count, 1)

    @mock.patch('routing.services.osrm.fetch_routes', return_value=[fixtures.ROUTE])
    def test_response_explains_data_and_cost_limitations(self, fetch):
        body = self.request_plan().json()
        self.assertEqual(body['data_quality']['station_locations'], 'approximate_city_centers')
        self.assertFalse(body['data_quality']['station_detours_included'])
        self.assertFalse(body['data_quality']['truck_access_verified'])
        self.assertEqual(body['optimizer']['driver_hourly_cost_usd'], 30)

    def test_map_reports_invalid_cost_without_blame_on_starting_fuel(self):
        response = self.client.get('/api/route/map/', {
            'start': 'Startville, KS', 'finish': 'Endtown, OH', 'driver_hourly_cost': 'nan',
        })
        self.assertContains(response, 'Stop cost and driving cost', status_code=400)
        self.assertNotContains(response, 'Fuel in the tank must', status_code=400)

