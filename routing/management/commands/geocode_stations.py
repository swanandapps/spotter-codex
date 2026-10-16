"""One-time build step: attach coordinates to every US station in the price list.

The price list has no coordinates, so each station is placed at its city's centroid:
  1. US Census 2024 place gazetteer (data/us_places.csv), offline, ~95% of rows
  2. Nominatim structured search for the rest (unincorporated places like
     'Breezewood, PA'), rate-limited to 1 req/s per the usage policy

Output is data/fuel_stations_geocoded.csv, committed to the repo, so reviewers and
the running API never need to geocode stations. Canadian rows are dropped (routes
are US-only). Rows sharing an OPIS id are collapsed, keeping the lowest price.
"""

import csv
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.services.geo import in_usa, normalize_place

from ._places import load_place_index

DATA = Path(settings.BASE_DIR) / "data"
OUT = DATA / "fuel_stations_geocoded.csv"


class Command(BaseCommand):
    help = "Geocode the fuel price CSV into data/fuel_stations_geocoded.csv"

    def handle(self, *args, **opts):
        places = load_place_index(DATA / "us_places.csv")
        us_states = {state for (_, state) in places}

        with open(DATA / "fuel-prices-for-be-assessment.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        by_id = {}
        for r in rows:
            state = r["State"].strip()
            if state not in us_states:
                continue
            opis_id = int(r["OPIS Truckstop ID"])
            price = float(r["Retail Price"])
            if opis_id not in by_id or price < float(by_id[opis_id]["Retail Price"]):
                by_id[opis_id] = r

        fallback_cache = {}
        out_rows, sources = [], {"gazetteer": 0, "nominatim": 0, "unresolved": 0}
        for r in by_id.values():
            city, state = r["City"].strip(), r["State"].strip()
            hit = places.get((normalize_place(city), state))
            source = "gazetteer"
            if hit is None:
                key = (city.lower(), state)
                if key not in fallback_cache:
                    fallback_cache[key] = self._nominatim(city, state)
                hit, source = fallback_cache[key], "nominatim"
            if hit is None:
                sources["unresolved"] += 1
                self.stderr.write(f"  unresolved: {city}, {state} ({r['Truckstop Name'].strip()})")
                continue
            sources[source] += 1
            out_rows.append(
                {
                    "opis_id": int(r["OPIS Truckstop ID"]),
                    "name": r["Truckstop Name"].strip(),
                    "address": r["Address"].strip(),
                    "city": city,
                    "state": state,
                    "rack_id": r["Rack ID"].strip(),
                    "price": r["Retail Price"].strip(),
                    "lat": round(hit[0], 6),
                    "lon": round(hit[1], 6),
                    "geocode_source": source,
                }
            )

        out_rows.sort(key=lambda x: x["opis_id"])
        with open(OUT, "w", newline="", encoding="utf-8") as g:
            w = csv.DictWriter(g, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(out_rows)} stations to {OUT} {sources}"))

    def _nominatim(self, city, state):
        time.sleep(1.1)
        try:
            resp = requests.get(
                settings.NOMINATIM_URL,
                params={"city": city, "state": state, "country": "USA", "format": "json", "limit": 1},
                headers={"User-Agent": settings.HTTP_USER_AGENT},
                timeout=settings.HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            results = resp.json()
        except (requests.RequestException, ValueError) as exc:
            self.stderr.write(f"  nominatim error for {city}, {state}: {exc}")
            return None
        if not results:
            return None
        lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
        return (lat, lon) if in_usa(lat, lon) else None
