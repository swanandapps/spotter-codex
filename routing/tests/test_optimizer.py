import math
import random

from django.test import SimpleTestCase

from routing.services.optimizer import Candidate, NoFeasiblePlan, habit_baseline, plan_fuel_stops


def greedy_min_cost(candidates, total, rng, mpg, start_fuel):
    """Textbook greedy for the pure min-cost gas station problem (reference oracle)."""
    st = sorted(candidates, key=lambda c: c.mile)
    pos, fuel, price, i, cost = 0.0, start_fuel, math.inf, -1, 0.0
    while True:
        reach = fuel if i < 0 else rng
        window = [j for j in range(i + 1, len(st)) if st[j].mile <= pos + reach + 1e-9]
        dest_ok = total - pos <= reach + 1e-9
        nxt = next((j for j in window if st[j].price < price and not (dest_ok and st[j].mile >= total)), None)
        if nxt is None and dest_ok:
            cost += max(0.0, total - pos - fuel) / mpg * price if i >= 0 else 0.0
            return cost
        if nxt is not None:
            target = st[nxt].mile - pos
        else:
            if not window:
                raise NoFeasiblePlan(pos, 0, rng)
            nxt = min(window, key=lambda j: st[j].price)
            target = rng
        bought = max(0.0, target - fuel)
        cost += bought / mpg * price if bought else 0.0
        fuel += bought - (st[nxt].mile - pos)
        pos, price, i = st[nxt].mile, st[nxt].price, nxt


class OptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stops(self):
        plan = plan_fuel_stops([Candidate(100, 3.0)], total_miles=400, range_miles=500, mpg=10)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)

    def test_buys_just_enough_at_expensive_then_fills_at_cheap(self):
        # Start full (500 mi). Must stop before mile 500. Station A (mile 450, $4) and
        # B (mile 480, $2). Optimal: skip A, fill at B, finish at mile 900.
        stations = [Candidate(450, 4.0), Candidate(480, 2.0)]
        plan = plan_fuel_stops(stations, total_miles=900, range_miles=500, mpg=10)
        self.assertEqual([s.candidate.mile for s in plan.stops], [480])
        self.assertAlmostEqual(plan.stops[0].gallons, (900 - 480 - 20) / 10)
        self.assertAlmostEqual(plan.total_cost, 40 * 2.0)

    def test_fills_up_at_cheap_station_to_avoid_expensive_one(self):
        stations = [Candidate(300, 2.0), Candidate(700, 5.0), Candidate(790, 5.0)]
        plan = plan_fuel_stops(stations, total_miles=1200, range_miles=500, mpg=10, stop_penalty=0)
        # At mile 300 fill to 500 mi range -> reaches 800; must buy 400 mi more at $5.
        self.assertAlmostEqual(plan.stops[0].gallons, 30.0)
        self.assertAlmostEqual(plan.total_cost, 30 * 2.0 + 40 * 5.0)

    def test_gap_beyond_range_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlan) as ctx:
            plan_fuel_stops([Candidate(400, 3.0)], total_miles=1000, range_miles=500, mpg=10)
        self.assertAlmostEqual(ctx.exception.at_mile, 400)
        self.assertAlmostEqual(ctx.exception.gap_miles, 600)

    def test_same_position_keeps_cheapest(self):
        plan = plan_fuel_stops(
            [Candidate(450, 3.5, "pricey"), Candidate(450, 3.1, "cheap")],
            total_miles=700, range_miles=500, mpg=10,
        )
        self.assertEqual([s.candidate.ref for s in plan.stops], ["cheap"])

    def test_zero_penalty_matches_greedy_optimum_on_random_routes(self):
        rnd = random.Random(42)
        checked = 0
        for _ in range(300):
            total = rnd.uniform(600, 3000)
            stations = [Candidate(rnd.uniform(0, total), rnd.uniform(2.5, 4.5)) for _ in range(rnd.randint(5, 60))]
            start = rnd.choice([500, 250])
            try:
                expected = greedy_min_cost(stations, total, 500, 10, start)
            except NoFeasiblePlan:
                with self.assertRaises(NoFeasiblePlan):
                    plan_fuel_stops(stations, total, 500, 10, start_fuel_miles=start, stop_penalty=0)
                continue
            plan = plan_fuel_stops(stations, total, 500, 10, start_fuel_miles=start, stop_penalty=0)
            self.assertAlmostEqual(plan.total_cost, expected, places=6)
            self.assertAlmostEqual(plan.total_gallons_bought, max(0, total - start) / 10, places=6)
            checked += 1
        self.assertGreater(checked, 100)

    def test_penalty_trades_pennies_for_fewer_stops(self):
        # Prices fall a cent every 20 miles: pure min-cost stops constantly.
        stations = [Candidate(m, 4.0 - m / 2000) for m in range(20, 2000, 20)]
        cheap = plan_fuel_stops(stations, 2000, 500, 10, stop_penalty=0)
        fewer = plan_fuel_stops(stations, 2000, 500, 10, stop_penalty=10)
        self.assertLess(len(fewer.stops), len(cheap.stops))
        self.assertLessEqual(len(fewer.stops), 4)
        self.assertGreaterEqual(fewer.total_cost, cheap.total_cost - 1e-9)

    def test_never_runs_dry(self):
        rnd = random.Random(7)
        for _ in range(100):
            total = rnd.uniform(600, 3000)
            stations = [Candidate(m, rnd.uniform(2.5, 4.5)) for m in range(0, int(total), 37)]
            plan = plan_fuel_stops(stations, total, 500, 10, stop_penalty=rnd.choice([0, 10, 50]))
            fuel, pos = 500.0, 0.0
            for s in plan.stops:
                fuel -= s.candidate.mile - pos
                self.assertGreaterEqual(fuel, -1e-6)
                self.assertAlmostEqual(fuel / 10, s.fuel_on_arrival_gallons, places=6)
                fuel += s.gallons * 10
                self.assertLessEqual(fuel, 500 + 1e-6)
                pos = s.candidate.mile
            self.assertGreaterEqual(fuel - (total - pos), -1e-6)


class HabitBaselineTests(SimpleTestCase):
    def test_refuels_at_first_station_after_quarter_tank(self):
        # Start full (500 mi). Quarter tank = 125 mi left, i.e. after mile 375.
        stations = [Candidate(300, 2.0), Candidate(380, 4.0), Candidate(450, 1.0)]
        plan = habit_baseline(stations, total_miles=800, range_miles=500, mpg=10,
                              start_fuel_miles=500, refuel_at_miles=125)
        self.assertEqual([s.candidate.mile for s in plan.stops], [380])  # price-blind
        self.assertAlmostEqual(plan.total_gallons_bought, 30.0)  # just enough to finish

    def test_buys_same_fuel_as_plan_and_never_beats_the_optimum(self):
        rnd = random.Random(3)
        compared = 0
        for _ in range(200):
            total = rnd.uniform(600, 3000)
            stations = [Candidate(rnd.uniform(0, total), rnd.uniform(2.5, 4.5)) for _ in range(rnd.randint(20, 80))]
            habit = habit_baseline(stations, total, 450, 10, start_fuel_miles=450, refuel_at_miles=75)
            try:
                best = plan_fuel_stops(stations, total, 450, 10, start_fuel_miles=450, stop_penalty=0)
            except NoFeasiblePlan:
                continue
            if habit is None:
                continue
            self.assertAlmostEqual(habit.total_gallons_bought, best.total_gallons_bought, places=6)
            self.assertGreaterEqual(habit.total_cost, best.total_cost - 1e-6)
            compared += 1
        self.assertGreater(compared, 100)


class IndependentFixedCostOracleTests(SimpleTestCase):
    def test_positive_stop_penalties_match_an_independent_mixed_integer_model(self):
        """Independent inventory constraints, not the optimizer's fill/just-enough states."""
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        rnd = random.Random(313)
        for case in range(40):
            total, capacity = 1200, 450
            start = rnd.choice([150, 300, 450])
            penalty = rnd.choice([0, 5, 10, 50])
            positions = sorted(rnd.sample(range(0, total, 10), 9))
            candidates = [Candidate(m, rnd.uniform(2, 5)) for m in positions]
            n = len(candidates)
            rows, lower, upper = [], [], []
            for i, candidate in enumerate(candidates):
                arrival = np.zeros(2 * n); arrival[:i] = 1
                rows.append(arrival); lower.append(candidate.mile - start); upper.append(np.inf)
                departure = np.zeros(2 * n); departure[:i + 1] = 1
                rows.append(departure); lower.append(-np.inf); upper.append(capacity + candidate.mile - start)
                activation = np.zeros(2 * n); activation[i] = 1; activation[n + i] = -capacity
                rows.append(activation); lower.append(-np.inf); upper.append(0)
            destination = np.zeros(2 * n); destination[:n] = 1
            rows.append(destination); lower.append(total - start); upper.append(np.inf)
            reference = milp(
                c=np.array([c.price / 10 for c in candidates] + [penalty] * n),
                integrality=np.array([0] * n + [1] * n),
                bounds=Bounds(np.zeros(2 * n), np.array([capacity] * n + [1] * n)),
                constraints=LinearConstraint(np.array(rows), np.array(lower), np.array(upper)),
                options={'time_limit': 3, 'mip_rel_gap': 0},
            )
            with self.subTest(case=case, penalty=penalty):
                if reference.status == 2:
                    with self.assertRaises(NoFeasiblePlan):
                        plan_fuel_stops(candidates, total, capacity, 10, start, penalty)
                else:
                    self.assertEqual(reference.status, 0, reference.message)
                    actual = plan_fuel_stops(candidates, total, capacity, 10, start, penalty)
                    self.assertAlmostEqual(actual.total_cost + penalty * len(actual.stops), reference.fun, places=5)
