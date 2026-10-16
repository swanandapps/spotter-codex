# Product and engineering review — October 15, 2026

This review concerns the independent `spotter-codex` copy. The original Claude project was read for comparison and was not edited during this iteration. The driver workflow below is a research-based hypothesis, not a claim that drivers were interviewed.

## Review of Claude's decisions

| Decision | Assessment |
|---|---|
| Pure optimizer with one route call and locally indexed stations | Strong foundation for the assignment. Retained. |
| Starting fuel and a 5-gallon reserve | Useful explicit inputs/assumptions. Retained, with clearer limits around unmeasured detours. |
| Cost per stop and driving-time weighting | Useful for avoiding tiny purchases and long alternatives. Exposed as editable preferences, with a genuine fuel-only preset. |
| Quarter-tank savings comparison | Useful as an explained simulation. It must not be described as measured driver behavior or guaranteed savings. |
| Dark teal interface and map/stop details | Coherent visual direction. The copy uses a lighter interface, clearer hierarchy, fixed details panels, explicit map picking and a mobile bottom sheet. Color preference alone does not determine UX quality. |
| Cache tied to price data | Correct intent. Full content hashing catches balanced price changes and address/coordinate corrections; separate road caching avoids another routing call when the fuel plan changes. |
| “9.5” in every scorecard category | A target is not validation. This review records tests and explicit limits rather than treating that score as evidence. |

## Driver workflow and product evidence

A likely preparation sequence is: understand the load and destination, check fuel aboard, select a usable route, decide where to buy fuel, and fit breaks and overnight parking around the trip. During travel, a planned station may be unsuitable or unavailable. At the stop, the useful details are the address, purchase quantity, expected cost and fuel needed for the next leg.

Trucker Path's [Smart Fuel Planner](https://helpcenter.truckerpath.com/hc/en-us/articles/38761048024077-Using-the-Smart-Fuel-Planner) supports pickup/drop-off/break stops, vehicle routing profiles, fuel networks, starting tank, tank capacity, MPG and minimum fill. Its itinerary shows fuel stops, purchase quantities and prices. That supports prioritizing trip context and usable stop decisions over a price-only ranking.

[Love's Connect](https://www.loves.com/promos/download-app-driver) offers shower check-in and commercial pump activation. [Pilot's professional-driver features](https://pilotcompany.com/pro-rewards) connect fueling with rewards and showers. These products show that a stop serves several needs; this dataset cannot verify those services at individual stations.

[FHWA's truck-parking material](https://ops.fhwa.dot.gov/freight/infrastructure/truck_parking/) identifies parking availability as an operational concern. Parking is a high-priority future integration, but a static station-price file cannot support an “available parking” badge.

## Implemented in this iteration

| Change | Practical benefit | Evidence |
|---|---|---|
| Separate road cache and shared in-flight request | Preferences, price updates and station exclusions avoid repeated OSRM calls. | Unit/concurrency tests and live API measurements. |
| Avoid station / restore controls | A driver or dispatcher can reject an unsuitable stop and see another plan. | API tests and mobile/desktop browser interaction. |
| Driving legs and road-segment timing | Each stop explains the drive from the previous one, with fuel at both ends. | Inventory/route-total tests and live route inspection. |
| Printable, downloadable trip brief | Addresses and purchases remain available without map tiles or a connection. | Actual browser download; self-contained HTML and escaping tests. |
| Inline failure recovery and discard edits | A failed request does not remove the displayed plan. | Below-reserve browser scenario. |
| Mobile map/stop jump controls | Less scrolling between the two main tasks. | 390 × 844 browser inspection. |
| Cent-consistent totals | Purchase amounts sum to the displayed total; savings use the same rounded totals. | Focused regression test and live route assertions. |
| Stable station IDs on data reload | Saved exclusions survive updated source prices. | Repeated transactional import test. |
| Provider response validation and paced geocoding | Upstream errors produce controlled API responses; local city lookup remains fast. | Malformed-response/cache/rate-pacing tests. |
| Gzip responses and visible request metrics | Smaller transfers and inspectable performance. | Live payload measurements in `docs/validation-results.json`. |
| Independent optimizer oracle | Tests the default fixed stop cost, beyond fuel-only greedy comparisons. | 40 seeded cases against a mixed-integer inventory model. |

The offline download verification was delayed by the browser's download interaction. The iteration should not be represented as one uninterrupted hour of implementation work.

## Next additions, in priority order

1. **Verified truck routing and station entrances.** Obtain truck restrictions and exact coordinates first, then account for detour fuel/time. This resolves the largest real-world reliability gap.
2. **Fleet fuel-card prices and accepted networks.** Optimize the actual price paid and filter networks with verified membership. A station-name heuristic is insufficient.
3. **Break and parking planning.** Integrate current duty-clock state, delivery windows, parking data and availability confidence. [FMCSA's HOS summary](https://www.fmcsa.dot.gov/regulations/hours-service/summary-hours-service-regulations) illustrates why a single total driving-time figure is not a legal-duty calculation.
4. **Pickup/drop-off waypoints.** Keep one routing request containing the required intermediate stops, then optimize fuel over the full route.
5. **Receipts and estimate-versus-actual costs.** Useful once the core route and price data are reliable; avoid adding account management just to demonstrate the assessment.

For production, replace process-local caching/coalescing with shared infrastructure, enforce provider-wide request limits, add operational monitoring, and select a supported truck-routing provider. These are outside the local assessment build.

## Scoped self-assessment

The target is **9.5/10 for the assessment implementation**, not production truck navigation, a user-research score, or completed submission packaging.

| Area | Weight | Self-score | Basis and remaining limit |
|---|---:|---:|---|
| API and assignment behavior | 20 | 20 | USA validation, route/map, multiple stops, fixed range/MPG, costs, current stable Django. Starting-fuel and reserve assumptions are explicit. |
| Calculation correctness | 30 | 28.5 | Independent optimization oracle, cost/inventory tests, road-distance alignment. Station positions remain approximate. |
| Efficiency and resilience | 20 | 19 | One new route call, bounded retry, separate caches, coalescing, gzip, recoverable failures. Measurements are local, not load-test guarantees. |
| UX and driver workflow | 20 | 18.5 | Desktop/mobile paths, clear purchases/legs, station avoidance, offline brief, keyboard/focus behavior. No driver usability study. |
| Maintainability and reviewability | 10 | 9 | Separated services/static assets, focused tests, documented API and demo outline. More production operational work remains. |
| **Total** | **100** | **95** | **9.5/10, scoped and self-assessed.** |

The app is not ready to be trusted as real truck navigation without verified access, restrictions, station locations and current prices. A GitHub submission and recorded Loom still require the candidate's final delivery steps.
