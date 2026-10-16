# Spotter Fuel Planner — independent version

This copy lives in `spotter-codex`, separately from the original `spotter` project. It retains the original Django routing/optimization foundation and adds a redesigned interface, driver workflow improvements, and correctness/performance fixes. Its database and environment are independent.

Enter two US locations to get a route, cost-effective fuel stops, gallons to buy, and total fuel purchases. The assignment vehicle has a 500-mile range at 10 mpg. The app assumes a full 50-gallon starting tank unless specified and retains a 5-gallon reserve.

Django **6.1.2**, verified against the [official download page](https://www.djangoproject.com/download/) on October 15, 2026. Dependencies are pinned in `requirements.txt`.

## Run locally

```bash
python3 -m venv .venv  # Python 3.12+
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_data
python manage.py runserver 127.0.0.1:8766
python manage.py test routing
```

The data import is offline and transactional. Reloading prices preserves station IDs using the source OPIS ID, so saved station exclusions survive reloads. Retired source stations are removed. The station-index fingerprint detects price, address, coordinate and other station changes, including bulk updates.

Open [the planner](http://127.0.0.1:8766/api/route/map/). Import `postman_collection.json` for the API demonstration. `LOOM_SCRIPT.md` is a five-minute recording outline; a Loom has not been recorded or published by this app.

## API

```text
POST /api/route/                 JSON trip inputs
GET  /api/route/?start=...&finish=...
GET  /api/route/map/?start=...&finish=...
GET  /api/route/brief/?start=...&finish=...              Printable trip brief
GET  /api/route/brief/?start=...&finish=...&download=1   Self-contained HTML
GET  /api/places/?q=chi                                Local autocomplete
GET  /api/places/nearest/?lat=...&lon=...               Local pin label
```

```json
{
  "start": "Chicago, IL",
  "finish": "Denver, CO",
  "start_fuel_gallons": 50,
  "stop_penalty": 10,
  "driver_hourly_cost": 30,
  "avoid_station_ids": []
}
```

| Input | Default | Meaning |
|---|---|---|
| `start`, `finish` | Required | US city/state, address, or `lat,lon`. |
| `start_fuel_gallons` | 50 | Fuel already aboard; 0–50 accepted, below the 5-gallon reserve returns 422. |
| `stop_penalty` | 10 | Comparison cost per purchase stop. Not added to the fuel bill. |
| `driver_hourly_cost` | 30 | Driving-time value for comparing returned routes. Not added to the fuel bill. |
| `route_option` | Recommended | Index in `route_options.options` to inspect a specific feasible alternative. |
| `avoid_station_ids` | Empty | Up to 20 station IDs to exclude. Repeat this query parameter in GET requests. |

Set **both cost preferences to zero** for the lowest fuel-purchase cost among the routes and stations considered. The default balances fuel, stop count and driving time. The optimizer is exact for its fixed-route candidate model; it does not explore every road in the country.

The response includes `route`, `vehicle`, `fuel_stops`, `legs`, `summary`, `route_options`, `excluded_stations`, `data_quality`, `map` (GeoJSON), `map_url`, and `meta`.

Each driving leg reports distance, estimated driving time, departure fuel and arrival fuel. Each purchase reports station/address, source price, gallons, cost, fuel inventory and a reason. The displayed trip cost sums the individual purchases rounded to cents. Fuel inventory is calculated at full precision and rounded for display, so displayed quantities can differ by a hundredth when added.

`summary.total_fuel_cost` means purchases made during this trip. Starting fuel is excluded. A short trip can therefore cost $0 in new purchases while consuming fuel already aboard. The quarter-tank savings figure compares with a specified simulation on the same route, not measured driver behavior or guaranteed savings.

Validation returns 400 for invalid input and 422 for infeasible fuel coverage/no drivable route. Routing/geocoding service failures return 502; an in-flight road request that cannot finish within its deadline returns 503. Non-finite numeric inputs are rejected.

## Interface

- Search US cities locally, type addresses, or set/drag individual endpoint pins; clear and swap are supported.
- Edit starting fuel, use Balanced/Lowest fuel cost presets, or enter custom costs.
- Read stops in driving order with distance and time **from the previous stop**.
- Inspect details in a desktop panel or a mobile bottom sheet; previous/next and keyboard controls are supported.
- Avoid an unsuitable station, recalculate, and restore individual exclusions or all of them.
- Retain the displayed plan on request failure; discard edits to return to it.
- Switch between the map and stop list quickly on mobile.
- Copy a trip URL or open a printable brief. The downloaded HTML includes inline styles and a route diagram and works without fonts, tiles, scripts or network access. External links still need a connection.
- Compare route alternatives and inspect a fuel-level chart. Map/Satellite/Terrain selection is retained locally.

## Performance and external services

A new city-to-city trip normally needs **one OSRM request**, including alternatives, geometry, segment distances and durations. A timeout/connection failure/5xx is retried once. Cities and coordinate inputs need no external geocoding.

Road geometry is cached separately for one hour. Changing fuel preferences, starting fuel, prices or station exclusions reuses that geometry. Complete plan bundles are cached for 24 hours; selecting an already planned alternative is a cache hit. Simultaneous identical road requests share one external call within the process. Gzip reduces JSON/HTML transfer size for clients that support it.

`meta` reports actual request elapsed time, routing and geocoding calls, plan/road cache hits and candidate counts. The interface preserves the initiating planning request's metrics when it subsequently opens the cached map. Results from three live routes and five warm requests per route are in `docs/validation-results.json`; these are local development observations, not production service guarantees.

Address lookup uses Nominatim only after local lookup fails. Successful lookups are cached for seven days. Misses are serialized with at least one second between requests in this single-process demo. Autocomplete never uses Nominatim. Its [usage policy](https://operations.osmfoundation.org/policies/nominatim/) requires application-wide rate limiting, an identifying User-Agent, attribution and caching. Before using multiple worker processes, provide a shared limiter/cache or a suitable hosted/self-hosted provider. `NOMINATIM_URL` and `OSRM_BASE_URL` are configurable. Do not submit confidential addresses to public geocoding services.

## Design and data limits

The supplied price CSV has no exact station coordinates. The prepared station file uses town centers from the US Census gazetteer and a limited set of Nominatim lookups. Runtime planning never geocodes all stations. The corridor is 10 miles around the route, using a KD-tree; station progress is aligned to OSRM road-distance annotations. Driving estimates use segment durations normalized to the route total, with proportional-distance fallback when annotations are absent.

The following limits are explicit in the UI, brief and JSON:

- Prices are the assessment dataset, not live prices; no price freshness timestamp is available.
- Town-center pins do not verify the forecourt, entrance, highway side, detour distance, opening hours, truck access, parking or amenities.
- The 50-mile reserve is maintained in the modeled route. **It does not guarantee enough fuel for an unmeasured station detour.**
- OSRM uses a standard driving profile, not vehicle height/weight/hazmat restrictions. US endpoints do not guarantee the entire road route stays inside the USA.
- Driving time excludes traffic, fueling, breaks and overnight rest. No hours-of-service compliance or delivery ETA is calculated.
- The public routing/geocoding services and process-local caches are suitable for this assessment; production needs provider and deployment decisions.

The bundled US boundary validates the 50 states and DC. Some islands or disconnected road networks can correctly return no route or no feasible fuel coverage.

## Code and verification

| File | Responsibility |
|---|---|
| `routing/views.py` | Validation, JSON/map/brief endpoints and response timing. |
| `routing/services/planner.py` | Station fingerprint, orchestration, route comparison, legs and cost accounting. |
| `routing/services/route_cache.py` | Independent road cache and concurrent request coalescing. |
| `routing/services/osrm.py` | Bounded retry, payload validation, road-distance/time annotations. |
| `routing/services/optimizer.py` | Pure fuel optimization and quarter-tank simulation. |
| `routing/services/brief.py` | Offline route diagram and itinerary context. |
| `routing/templates/routing/map.html` | Main page structure. |
| `routing/static/routing/planner.js` | Interaction, request recovery, map, stop cards and chart. |
| `routing/static/routing/planner.css` | Desktop and mobile layout. |

The suite includes randomized fuel-only comparisons, inventory/reserve checks, an independent mixed-integer optimization oracle for stop penalties, caching/concurrency tests, exclusion and restoration, stable data imports, malformed provider responses, non-finite inputs, and offline-brief escaping. Browser checks cover desktop and mobile, station exclusion/restoration, request recovery and the actual offline download.

See `DESIGN_REVIEW.md` for the product review, source research, remaining priorities and scoped self-assessment.
