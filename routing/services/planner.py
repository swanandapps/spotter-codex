"""Orchestrates one trip plan: geocode -> route (1 external call) -> corridor -> optimise."""

import dataclasses
import hashlib
import threading
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import numpy as np
from django.conf import settings
from django.core.cache import cache

from ..models import FuelStation
from . import geocode, osrm, places, route_cache
from .corridor import stations_along_route
from .geo import thin
from .optimizer import Candidate, NoFeasiblePlan, habit_baseline, plan_fuel_stops

CACHE_VERSION = "v8"


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _purchase_total(plan):
    """Each purchase is paid in cents; the trip total is the sum of those payments."""
    return _money(sum(_money(stop.cost) for stop in plan.stops))


class StationIndex:
    """All stations held in memory as numpy arrays (~6.6k rows).

    A content fingerprint also versions cached plans. Reading these ~6.6k rows
    catches bulk updates, swapped prices, and corrected coordinates or addresses.
    The index is built from the same snapshot that was hashed.
    """

    _lock = threading.Lock()
    _instance = None

    FIELDS = ("id", "opis_id", "name", "address", "city", "state", "price", "lat", "lon")

    def __init__(self, version, rows):
        self.version = version
        self.rows = rows
        self.lat = np.array([r[7] for r in self.rows], dtype=float)
        self.lon = np.array([r[8] for r in self.rows], dtype=float)
        self.price = np.array([float(r[6]) for r in self.rows], dtype=float)

    @classmethod
    def get(cls):
        rows = list(FuelStation.objects.order_by("pk").values_list(*cls.FIELDS))
        version = hashlib.sha256(repr(rows).encode()).hexdigest()
        if cls._instance is None or cls._instance.version != version:
            with cls._lock:
                if cls._instance is None or cls._instance.version != version:
                    cls._instance = cls(version, rows)
                return cls._instance
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None

    def as_dict(self, i):
        pk, opis_id, name, address, city, state, price, lat, lon = self.rows[i]
        return {
            "id": pk,
            "opis_id": opis_id,
            "name": name,
            "address": address,
            "city": city,
            "state": state,
            "price_per_gallon": float(price),
            "lat": lat,
            "lon": lon,
        }


# Brand recognition from the station name. The first group are national truck-stop
# networks built for big rigs (truck parking, diesel lanes); the rest are
# convenience chains where access for a 53-ft trailer varies by location.
TRUCK_STOP_CHAINS = [
    ("PILOT", "Pilot"), ("FLYING J", "Flying J"), ("LOVE", "Love's"), ("PETRO", "Petro"),
    ("TA ", "TA"), ("TRAVELCENTERS OF AMERICA", "TA"), ("AMBEST", "AmBest"),
    ("SAPP BROS", "Sapp Bros."), ("ROADY", "Roady's"), ("BOSSELMAN", "Bosselman"),
]
OTHER_CHAINS = [
    ("KWIK TRIP", "Kwik Trip"), ("KWIK STAR", "Kwik Star"), ("CASEY", "Casey's"), ("SHEETZ", "Sheetz"),
    ("QUIKTRIP", "QuikTrip"), ("MAVERIK", "Maverik"), ("KUM & GO", "Kum & Go"), ("BUC-EE", "Buc-ee's"),
    ("SPEEDWAY", "Speedway"), ("CIRCLE K", "Circle K"), ("7-ELEVEN", "7-Eleven"), ("WAWA", "Wawa"),
    ("RUTTER", "Rutter's"), ("STRIPES", "Stripes"), ("ONE9", "ONE9"), ("ROAD RANGER", "Road Ranger"),
]


def _chain(name):
    upper = name.upper() + " "
    for needle, brand in TRUCK_STOP_CHAINS:
        if upper.startswith(needle) or f" {needle}" in upper:
            return brand, True
    for needle, brand in OTHER_CHAINS:
        if needle in upper:
            return brand, False
    return None, False


class PlanError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def plan_trip(start, finish, stop_penalty=None, start_fuel_gallons=None, route_option=None, driver_hourly_cost=None,
              avoid_station_ids=None):
    mpg = settings.VEHICLE_MPG
    tank_gallons = settings.VEHICLE_RANGE_MILES / mpg
    reserve = settings.FUEL_RESERVE_MILES
    if stop_penalty is None:
        stop_penalty = settings.FUEL_STOP_PENALTY_USD
    if driver_hourly_cost is None:
        driver_hourly_cost = settings.DRIVER_TIME_USD_PER_HOUR
    if start_fuel_gallons is None:
        start_fuel_gallons = tank_gallons
    if start_fuel_gallons * mpg < reserve:
        raise PlanError(
            f"The truck starts with {start_fuel_gallons:g} gal, below the {reserve / mpg:g}-gal reserve. "
            "Fuel up before departing.",
            status=422,
        )

    try:
        start = geocode.resolve(start)
        finish = geocode.resolve(finish)
    except geocode.GeocodingError as exc:
        raise PlanError(str(exc), status=exc.status) from exc
    start, finish = (_name_pin(loc) for loc in (start, finish))
    if start.point == finish.point:
        raise PlanError("Start and finish are the same place; enter two different locations.", status=400)

    index = StationIndex.get()
    avoided = tuple(sorted(set(avoid_station_ids or ())))
    station_ids = {row[0] for row in index.rows}
    if any(pk not in station_ids for pk in avoided):
        raise PlanError("One of the excluded stations no longer exists. Remove the exclusion and try again.", status=400)
    key = _cache_key(start.point, finish.point, stop_penalty, start_fuel_gallons, index.version, driver_hourly_cost, avoided, (start.label, finish.label))
    geocoding_calls = sum(loc.source == "nominatim" for loc in (start, finish))
    cached = cache.get(key)
    if cached is not None:
        result = _pick(cached, route_option)
        meta = {**result["meta"], "cache_hit": True, "road_cache_hit": True,
                "routing_api_calls": 0, "geocoding_api_calls": geocoding_calls}
        return {**_location_sources(result, start, finish), "meta": meta}

    try:
        routes, road_cache_hit = route_cache.get_routes(start.point, finish.point)
    except osrm.RoutingError as exc:
        raise PlanError(str(exc), status=exc.status) from exc
    if routes[0].distance_miles < 1:
        raise PlanError("Start and finish are the same place; enter two different locations.", status=400)

    # Plan fuel on every route OSRM returned (one request), then keep the one with the
    # lowest real cost. Every plan ends at the same reserve, so fuel cost already pays
    # for any extra miles; stops and driving time are priced in so a route can't win by
    # saving $3 of diesel while adding an hour and a half.
    options, first_error = [], None
    for k, route in enumerate(routes):
        try:
            options.append(_plan_route(k, route, index, stop_penalty, start_fuel_gallons, driver_hourly_cost, avoided))
        except NoFeasiblePlan as exc:
            first_error = first_error or exc
    if not options:
        exc = first_error
        raise PlanError(
            ("With these stations excluded, no" if avoided else "No") +
            f" fuel station is within reach after mile {exc.at_mile:,.0f}: the next gap is {exc.gap_miles:,.0f} miles, "
            f"more than the {exc.reach_miles:,.0f} miles the truck can cover from there without dipping into "
            f"its {reserve}-mile reserve.",
            status=422,
        ) from exc
    best = min(options, key=lambda o: o["score"])

    # Build the full response for every feasible route now (cheap: the plans exist),
    # so viewing an alternate's fuel plan later is a cache hit, not another OSRM call.
    route_options = _route_options(options, best, routes, stop_penalty, driver_hourly_cost)
    results = {}
    for o in options:
        result = _build_response(
            start, finish, o["route"], o["plan"], o["baseline"], o["candidates"], index,
            stop_penalty, start_fuel_gallons,
        )
        result["route_options"] = {**route_options, "viewing": o["option"], "recommended": best["option"]}
        result["optimizer"]["driver_hourly_cost_usd"] = driver_hourly_cost
        result["map"]["features"][1:1] = [
            {
                "type": "Feature",
                "properties": {"kind": "alternate_route", "option": other["option"]},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in thin(other["route"].coordinates)],
                },
            }
            for other in options if other is not o
        ]
        result["meta"]["geocoding_api_calls"] = geocoding_calls
        result["meta"]["road_cache_hit"] = road_cache_hit
        result["meta"]["routes_compared"] = len(options)
        result["excluded_stations"] = [index.as_dict(i) for i, row in enumerate(index.rows) if row[0] in avoided]
        results[o["option"]] = result
    bundle = {"results": results, "recommended": best["option"]}
    cache.set(key, bundle)
    return _location_sources(_pick(bundle, route_option), start, finish)


def _location_sources(result, start, finish):
    return {**result, "start": {**result["start"], "resolved_by": start.source},
            "finish": {**result["finish"], "resolved_by": finish.source}}


def _pick(bundle, route_option):
    """The recommended route's plan, or the one the caller asked to look at."""
    if route_option is None:
        return bundle["results"][bundle["recommended"]]
    if route_option not in bundle["results"]:
        available = ", ".join(str(k) for k in sorted(bundle["results"]))
        raise PlanError(f"Route option {route_option} isn't available for this trip (options: {available}).", status=400)
    return bundle["results"][route_option]


def _plan_route(k, route, index, stop_penalty, start_fuel_gallons, driver_hourly_cost, avoided=()):
    """Corridor search + optimal fuel plan + habit baseline for one candidate route."""
    reserve = settings.FUEL_RESERVE_MILES
    on_route = stations_along_route(route.coordinates, index.lat, index.lon, settings.ROUTE_CORRIDOR_MILES,
                                  route.distance_miles, route.segment_distances_miles)
    on_route = [station for station in on_route if index.rows[station.index][0] not in avoided]
    candidates = [Candidate(mile=s.route_mile, price=index.price[s.index], ref=s) for s in on_route]
    # The optimiser works in "usable" miles: the reserve is never planned for use,
    # so the truck effectively has a (range - reserve) tank and starts with
    # (start fuel - reserve). Reserve is added back for display.
    usable = dict(
        total_miles=route.distance_miles,
        range_miles=settings.VEHICLE_RANGE_MILES - reserve,
        mpg=settings.VEHICLE_MPG,
        start_fuel_miles=start_fuel_gallons * settings.VEHICLE_MPG - reserve,
    )
    plan = plan_fuel_stops(candidates, stop_penalty=stop_penalty, **usable)
    baseline = habit_baseline(candidates, refuel_at_miles=settings.VEHICLE_RANGE_MILES / 4 - reserve, **usable)
    hours = route.duration_seconds / 3600
    score = plan.total_cost + stop_penalty * len(plan.stops) + driver_hourly_cost * hours
    return {"option": k, "route": route, "plan": plan, "baseline": baseline, "candidates": candidates,
            "hours": hours, "score": score}


def _route_options(options, best, routes, stop_penalty, driver_hourly_cost):
    """Every route considered, cheapest-in-real-terms first, with the trade-off vs the chosen one."""
    rows = []
    for o in sorted(options, key=lambda o: o["score"]):
        extra_cost = _purchase_total(o["plan"]) - _purchase_total(best["plan"])
        extra_hours = o["hours"] - best["hours"]
        rows.append({
            "option": o["option"],
            "chosen": o is best,
            "fastest": o["option"] == 0,
            "distance_miles": round(o["route"].distance_miles, 1),
            "duration_hours": round(o["hours"], 2),
            "fuel_stops": len(o["plan"].stops),
            "total_fuel_cost": _purchase_total(o["plan"]),
            "fuel_cost_vs_chosen": round(extra_cost, 2),
            "hours_vs_chosen": round(extra_hours, 2),
            "comparison_cost": round(o["score"], 2),
        })
    skipped = len(routes) - len(options)
    return {
        "compared_on": (
            f"fuel cost + ${stop_penalty:g}/stop + driver time at "
            f"${driver_hourly_cost:g}/hour"
        ),
        "options": rows,
        "infeasible_routes": skipped,
    }


def _name_pin(loc):
    """A dropped pin arrives as 'lat,lon'; label it 'Pin near Joliet, IL' for people."""
    if loc.source != "coordinates":
        return loc
    near = places.nearest(loc.lat, loc.lon)
    return dataclasses.replace(loc, label=f"Pin near {near['label']}") if near else loc


def _cache_key(a, b, stop_penalty, start_fuel, data_version, driver_hourly_cost, avoided=(), labels=()):
    raw = (
        f"{CACHE_VERSION}:{a[0]:.5f},{a[1]:.5f}:{b[0]:.5f},{b[1]:.5f}:"
        f"{stop_penalty!r}:{start_fuel!r}:{driver_hourly_cost!r}:{data_version}:"
        f"{settings.VEHICLE_MPG}:{settings.VEHICLE_RANGE_MILES}:{settings.FUEL_RESERVE_MILES}:"
        f"{settings.ROUTE_CORRIDOR_MILES}:{avoided!r}:{settings.OSRM_BASE_URL}:{labels!r}"
    )
    return "plan:" + hashlib.sha1(raw.encode()).hexdigest()


def _reason(k, plan, candidates, index, finish_label, full_tank_miles, mpg):
    """One true sentence on why the plan stops here, from the plan's own structure.

    An optimal plan only ever fills up (next stop is pricier) or buys just enough to
    reach the next stop (it is cheaper), so the explanation follows directly.
    """
    stop = plan.stops[k]
    if k + 1 == len(plan.stops):
        ahead = [c.price for c in candidates if c.mile > stop.candidate.mile]
        if ahead and min(ahead) > stop.candidate.price:
            return f"Buys just enough to reach {finish_label}: every truck stop left on the route charges more."
        return f"Buys just enough to reach {finish_label} with the reserve intact."
    nxt = plan.stops[k + 1].candidate
    nxt_station = index.as_dict(nxt.ref.index)
    where = f"{nxt_station['city']}, {nxt_station['state']}"
    filled = (stop.fuel_on_arrival_gallons + stop.gallons) * mpg >= full_tank_miles - 0.5
    if filled and nxt.price > stop.candidate.price:
        return f"Fills the tank: cheaper than the next stop in {where} (${nxt.price:.3f})."
    if filled:
        return f"Fills the tank so the truck reaches {where} without another stop."
    return f"Buys just enough to reach {where}, where diesel is ${nxt.price:.3f}."


def _build_response(start, finish, route, plan, baseline, candidates, index, stop_penalty, start_fuel_gallons):
    mpg = settings.VEHICLE_MPG
    reserve_gal = settings.FUEL_RESERVE_MILES / mpg
    usable_range = settings.VEHICLE_RANGE_MILES - settings.FUEL_RESERVE_MILES
    hours = route.duration_seconds / 3600
    finish_label = "the destination" if finish.source == "coordinates" else finish.label.split(",")[0]

    route_avg = float(np.mean([c.price for c in candidates])) if candidates else None
    stops = []
    for k, stop in enumerate(plan.stops):
        on_route = stop.candidate.ref
        station = index.as_dict(on_route.index)
        query = f"{station['name']}, {station['address']}, {station['city']}, {station['state']}"
        chain, truck_stop = _chain(station["name"])
        next_mile = plan.stops[k + 1].candidate.mile if k + 1 < len(plan.stops) else route.distance_miles
        stops.append(
            {
                "stop_number": k + 1,
                "station": station,
                "route_mile": round(on_route.route_mile, 1),
                "miles_off_route": round(on_route.offset_miles, 1),
                "drive_hours_from_start": round(route.time_at_mile(on_route.route_mile) / 3600, 2),
                "fuel_on_arrival_gallons": round(stop.fuel_on_arrival_gallons + reserve_gal, 2),
                "fuel_on_departure_gallons": round(stop.fuel_on_arrival_gallons + reserve_gal + stop.gallons, 2),
                "gallons_purchased": round(stop.gallons, 2),
                "cost": _money(stop.cost),
                "reason": _reason(k, plan, candidates, index, finish_label, usable_range, mpg),
                "navigation_url": "https://www.google.com/maps/search/?api=1&query=" + quote(query),
                "chain": chain,
                "truck_stop_chain": truck_stop,
                "cents_vs_route_average": round((station["price_per_gallon"] - route_avg) * 100, 1),
                "miles_to_next": round(next_mile - on_route.route_mile, 1),
                "next_is_destination": k + 1 == len(plan.stops),
            }
        )

    bought = plan.total_gallons_bought
    arrival_gal = start_fuel_gallons + bought - route.distance_miles / mpg
    legs = []
    previous_mile, previous_time, departure_fuel = 0.0, 0.0, start_fuel_gallons
    previous_label = start.label
    destinations = [
        (s.candidate.mile, stops[i]["station"]["name"], i + 1, s.gallons)
        for i, s in enumerate(plan.stops)
    ] + [(route.distance_miles, finish.label, None, 0)]
    for mile, label, stop_number, gallons in destinations:
        elapsed = route.time_at_mile(mile)
        distance = mile - previous_mile
        fuel_on_arrival = departure_fuel - distance / mpg
        legs.append({
            "leg_number": len(legs) + 1, "from": previous_label, "to": label,
            "to_stop_number": stop_number, "distance_miles": round(distance, 1),
            "driving_hours": round((elapsed - previous_time) / 3600, 2),
            "departure_fuel_gallons": round(departure_fuel, 2),
            "arrival_fuel_gallons": round(fuel_on_arrival, 2),
        })
        previous_mile, previous_time, previous_label = mile, elapsed, label
        departure_fuel = fuel_on_arrival + gallons
    savings = None
    if baseline is not None:
        baseline_cost = _purchase_total(baseline)
        saved = baseline_cost - _purchase_total(plan)
        savings = {
            "compared_with": "Filling up at the next truck stop whenever the tank is down to a quarter",
            "habit_stops": len(baseline.stops),
            "habit_fuel_cost": baseline_cost,
            "saved_usd": round(saved, 2),
            "saved_percent": round(100 * saved / baseline.total_cost, 1) if baseline.total_cost else 0.0,
            "saved_per_mile": round(saved / route.distance_miles, 4),
        }

    features = [
        {
            "type": "Feature",
            "properties": {"kind": "route", "distance_miles": round(route.distance_miles, 1)},
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in thin(route.coordinates)],
            },
        },
        _point(start, "start", start.label),
        _point(finish, "finish", finish.label),
    ]
    for s in stops:
        st = s["station"]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "fuel_stop",
                    "stop_number": s["stop_number"],
                    "name": st["name"],
                    "price_per_gallon": st["price_per_gallon"],
                    "gallons_purchased": s["gallons_purchased"],
                    "cost": s["cost"],
                },
                "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
            }
        )

    return {
        "start": {"query": start.label, "lat": start.lat, "lon": start.lon, "resolved_by": start.source},
        "finish": {"query": finish.label, "lat": finish.lat, "lon": finish.lon, "resolved_by": finish.source},
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(hours, 2),
        },
        "vehicle": {
            "range_miles": settings.VEHICLE_RANGE_MILES,
            "mpg": mpg,
            "tank_gallons": settings.VEHICLE_RANGE_MILES / mpg,
            "starting_fuel_gallons": start_fuel_gallons,
            "reserve_gallons": reserve_gal,
        },
        "optimizer": {
            "objective": "minimise fuel cost + stop_penalty per stop, never dipping into the reserve",
            "stop_penalty_usd": stop_penalty,
        },
        "fuel_stops": stops,
        "legs": legs,
        "summary": {
            "number_of_stops": len(stops),
            "total_fuel_cost": _purchase_total(plan),
            "total_gallons_purchased": round(bought, 2),
            "total_gallons_used": round(route.distance_miles / mpg, 2),
            "starting_fuel_gallons": start_fuel_gallons,
            "fuel_at_arrival_gallons": round(arrival_gal, 2),
            "average_price_paid": round(plan.total_cost / bought, 3) if bought else None,
            "fuel_cost_per_mile": round(plan.total_cost / bought / mpg, 4) if bought else None,
        },
        "savings": savings,
        "data_quality": {
            "station_locations": "approximate_city_centers",
            "prices": "supplied_assessment_dataset_not_live",
            "station_detours_included": False,
            "truck_access_verified": False,
            "stop_driving_times": "estimated_from_road_segments" if route.time_profile else "estimated_from_route_distance",
            "driving_time_excludes": ["fuel_stops", "breaks", "traffic", "overnight_rest"],
            "route_profile": "standard_driving_not_truck_restriction_aware",
            "fuel_cost_basis": "purchases_during_trip_excludes_starting_tank",
        },
        "map": {"type": "FeatureCollection", "features": features},
        "meta": {
            "stations_on_route_considered": len(candidates),
            "routing_api_calls": route.api_calls,
            "cache_hit": False,
        },
    }


def _point(loc, kind, label):
    return {
        "type": "Feature",
        "properties": {"kind": kind, "label": label},
        "geometry": {"type": "Point", "coordinates": [loc.lon, loc.lat]},
    }
