# Validation record — October 15, 2026

71 automated tests passed, including 40 randomized comparisons with an independent mixed-integer optimizer. Browser checks covered 1280 × 800 desktop and 390 × 844 mobile layouts, stop exclusion and restoration, below-reserve error recovery, and the actual self-contained HTML download.

| Route | New-plan time | Warm median (5 calls) | Routing calls, new / warm | Fuel stops | Fuel purchases |
|---|---:|---:|---:|---:|---:|
| Chicago, IL → Denver, CO | 3.83 s | 34.2 ms | 1 / 0 | 2 | $156.45 |
| New York, NY → Los Angeles, CA | 4.22 s | 37.0 ms | 1 / 0 | 7 | $708.79 |
| Dallas, TX → Austin, TX | 11.07 s | 32.0 ms | 1 / 0 | 0 | $0.00 |

Preference changes and station exclusions reused the road cache with zero routing calls. The cross-country JSON transferred 76,475 bytes with gzip versus 231,985 bytes without compression (about 67% smaller). Measurements include local API processing; the cold values include the free routing service. They are observations, not latency guarantees.

The downloaded brief was verified at `/Users/swanandkadam/Downloads/spotter-trip-brief.html`. It has inline styles and an inline SVG route, with no external font, image or script dependency. No Loom or GitHub publication was performed.

Raw measurements: `validation-results.json`.
