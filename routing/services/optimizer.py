"""Cheapest refuelling plan along a fixed route.

Objective: minimise  fuel cost + stop_penalty x number_of_stops.

With stop_penalty = 0 this is the textbook "gas station problem" and gives the
cheapest possible fuel bill, but it happily stops for 1 gallon to save $0.006/gal.
A small penalty (default $10, roughly the value of 15 minutes of a driver's time)
removes those stops while costing a few dollars at most.

Exact algorithm (Khuller, Malekian & Mestre, "To Fill or Not to Fill", 2007):
an optimal plan, at every stop, either fills the tank or buys just enough to reach
the next stop. So the fuel you arrive with at a stop is either 0, or
`range - distance` from an earlier stop where you filled up, or what is left of the
starting tank. That gives a small finite state space, (station, arrival fuel), and a
DP over stations in route order. A prefix-minimum trick (see the loop) makes each
transition a single binary search, so a cross-country trip with ~250 candidate
stops plans in ~10 ms.

All distances are in miles; fuel is tracked as "miles of range" and converted to
gallons only when pricing a purchase.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass

EPS = 1e-6


class NoFeasiblePlan(Exception):
    def __init__(self, at_mile, gap_miles, reach_miles):
        self.at_mile = at_mile
        self.gap_miles = gap_miles
        self.reach_miles = reach_miles  # starting fuel on the first leg, full range after
        super().__init__(
            f"No fuel station reachable after mile {at_mile:.0f}: "
            f"the next gap is {gap_miles:.0f} miles, more than the {reach_miles:.0f} miles in reach."
        )


@dataclass(frozen=True)
class Candidate:
    mile: float  # distance along the route
    price: float  # USD per gallon
    ref: object = None  # caller's station object, passed through untouched


@dataclass(frozen=True)
class Stop:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass(frozen=True)
class FuelPlan:
    stops: list
    total_gallons_bought: float
    total_cost: float


def plan_fuel_stops(candidates, total_miles, range_miles, mpg, start_fuel_miles=None, stop_penalty=0.0):
    start_fuel = float(range_miles if start_fuel_miles is None else start_fuel_miles)
    stations = _cheapest_per_position(candidates, total_miles)
    miles = [c.mile for c in stations]
    n = len(stations)
    DEST = n  # pseudo-index for the destination

    def pos(i):
        return total_miles if i == DEST else miles[i]

    # states[i]: {arrival_fuel_key: (arrival_fuel, cost_so_far, parent)}
    # parent = (station_index, parent_state, miles_bought) or None for the origin.
    states = [{} for _ in range(n + 1)]

    def relax(j, fuel, cost, parent):
        key = round(fuel, 6)
        current = states[j].get(key)
        if current is None or cost < current[1] - EPS:
            states[j][key] = (fuel, cost, parent)

    # From the origin we cannot buy; drive on the starting fuel to any first stop.
    if total_miles <= start_fuel + EPS:
        relax(DEST, start_fuel - total_miles, 0.0, None)
    for j in range(bisect_right(miles, start_fuel + EPS)):
        relax(j, start_fuel - miles[j], 0.0, None)

    for i in range(n):
        if not states[i]:
            continue
        price = stations[i].price / mpg  # USD per mile of range
        # Buying b miles from a state (g, c) costs c + b*price. Both options
        # ("just enough for distance d": b = d - g, "fill up": b = range - g) give
        # c - g*price + const, so per target we only need the minimum of
        # c - g*price over states with g < d: a prefix minimum over states by fuel.
        arrivals = sorted(states[i].values(), key=lambda s: s[0])
        fuels = [s[0] for s in arrivals]
        prefix_best = []
        for s in arrivals:
            score = s[1] - s[0] * price
            if not prefix_best or score < prefix_best[-1][0]:
                prefix_best.append((score, s))
            else:
                prefix_best.append(prefix_best[-1])

        def cheapest_below(limit):
            k = bisect_right(fuels, limit - EPS)
            return prefix_best[k - 1] if k else None

        reach_end = bisect_right(miles, miles[i] + range_miles + EPS)
        targets = list(range(i + 1, reach_end))
        if total_miles - miles[i] <= range_miles + EPS:
            targets.append(DEST)
        fill = cheapest_below(range_miles)

        for j in targets:
            d = pos(j) - miles[i]
            just_enough = cheapest_below(d)
            if just_enough is not None:
                score, state = just_enough
                relax(j, 0.0, score + d * price + stop_penalty, (i, state, d - state[0]))
            if fill is not None and j != DEST:
                score, state = fill
                relax(j, range_miles - d, score + range_miles * price + stop_penalty,
                      (i, state, range_miles - state[0]))

    if not states[DEST]:
        raise NoFeasiblePlan(*_first_gap(miles, total_miles, range_miles, start_fuel))

    best = min(states[DEST].values(), key=lambda s: s[1])
    stops = []
    parent = best[2]
    while parent is not None:
        i, state, bought = parent
        gallons = bought / mpg
        stops.append(Stop(stations[i], gallons, gallons * stations[i].price, state[0] / mpg))
        parent = state[2]
    stops.reverse()

    return FuelPlan(
        stops=stops,
        total_gallons_bought=sum(s.gallons for s in stops),
        total_cost=sum(s.cost for s in stops),
    )


def habit_baseline(candidates, total_miles, range_miles, mpg, start_fuel_miles, refuel_at_miles):
    """What a driver does without a plan, used to measure what the optimiser saves.

    Price-blind: once fuel is down to `refuel_at_miles`, or the next truck stop would
    be out of reach, stop at the next one and fill up, buying only enough to finish
    once the destination is in range. Where a town has several stations it takes the
    cheapest, which flatters the habit and keeps the savings figure conservative.
    Buys exactly as many gallons as the optimised plan, so costs compare directly.
    Returns None if the habit would run dry.
    """
    stations = _cheapest_per_position(candidates, total_miles)
    fuel, pos, stops = float(start_fuel_miles), 0.0, []
    for k, s in enumerate(stations):
        fuel -= s.mile - pos
        pos = s.mile
        if fuel < -EPS:
            return None
        remaining = total_miles - pos
        if fuel >= remaining - EPS:
            break
        next_mile = stations[k + 1].mile if k + 1 < len(stations) else total_miles
        if fuel <= refuel_at_miles + EPS or next_mile - pos > fuel + EPS:
            bought = min(range_miles, remaining) - fuel
            if bought > EPS:
                stops.append(Stop(s, bought / mpg, bought / mpg * s.price, fuel / mpg))
                fuel += bought
    if fuel < total_miles - pos - EPS:
        return None
    return FuelPlan(
        stops=stops,
        total_gallons_bought=sum(s.gallons for s in stops),
        total_cost=sum(s.cost for s in stops),
    )


def _cheapest_per_position(candidates, total_miles):
    """Stations at the same route mile (same city centroid) collapse to the cheapest."""
    best = {}
    for c in candidates:
        if 0 <= c.mile < total_miles:
            key = round(c.mile, 3)
            if key not in best or c.price < best[key].price:
                best[key] = c
    return sorted(best.values(), key=lambda c: c.mile)


def _first_gap(miles, total_miles, range_miles, start_fuel):
    points = [0.0] + miles + [total_miles]
    reach = start_fuel
    for a, b in zip(points, points[1:]):
        if b - a > reach + EPS:
            return a, b - a, reach
        reach = range_miles
    return 0.0, math.inf, range_miles
