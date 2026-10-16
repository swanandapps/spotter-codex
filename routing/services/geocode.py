"""Resolve a user-supplied location to (lat, lon).

Order of preference, cheapest first:
  1. "lat,lon"         -> parsed, no lookup
  2. "City, ST"        -> local US Census gazetteer table, no network (places missing
                          from it, e.g. unincorporated towns, fall through to 3)
  3. anything else     -> one Nominatim request (free, no key), restricted to the US
"""

import re
import hashlib
import math
import time
import threading
from dataclasses import dataclass, replace

import requests
from django.conf import settings
from django.core.cache import cache

from ..models import Place
from .geo import display_place, in_usa, normalize_place

_network_lock = threading.Lock()
_last_request_at = None

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_CITY_STATE = re.compile(r"^\s*(.+?)\s*,\s*([A-Za-z]{2})\s*(?:,\s*(?:usa?|united states))?\s*$", re.I)


US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


class GeocodingError(Exception):
    status = 400  # the input can't be resolved


class GeocodingServiceError(GeocodingError):
    status = 502  # the geocoding service itself failed


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    @property
    def point(self):
        return (self.lat, self.lon)


def resolve(text):
    text = (text or "").strip()
    if not text:
        raise GeocodingError("Location is empty.")

    m = _LATLON.match(text)
    city_state = _CITY_STATE.match(text)
    if m:
        loc = Location(float(m.group(1)), float(m.group(2)), text, "coordinates")
    elif city_state:
        city, state = city_state.group(1), city_state.group(2).upper()
        if state not in US_STATES:
            raise GeocodingError(f"'{text}': '{state}' is not a US state; locations must be in the USA.")
        loc = _from_gazetteer(city, state) or _from_nominatim({"q": text, "countrycodes": "us"}, text)
    else:
        loc = _from_nominatim({"q": text, "countrycodes": "us"}, text)

    if not in_usa(loc.lat, loc.lon):
        raise GeocodingError(f"'{text}' is outside the USA (50 states and DC); start and finish must both be in the US.")
    return loc


def _from_gazetteer(city, state):
    place = Place.objects.filter(normalized=normalize_place(city), state=state).first()
    if place is None:
        return None
    return Location(place.lat, place.lon, f"{display_place(place.name)}, {place.state}", "gazetteer")


def _from_nominatim(query, text):
    key = "geocode:" + hashlib.sha256((settings.NOMINATIM_URL + repr(sorted(query.items()))).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return replace(cached, source="nominatim_cache")
    # The public service allows one request per second across this local demo.
    # Serialize misses and re-check the cache after waiting for another caller.
    with _network_lock:
        cached = cache.get(key)
        if cached is not None:
            return replace(cached, source="nominatim_cache")
        _wait_for_request_slot()
        loc = _fetch_nominatim(query, text)
        if in_usa(loc.lat, loc.lon):
            cache.set(key, loc, timeout=settings.GEOCODING_CACHE_SECONDS)
        return loc


def _wait_for_request_slot():
    global _last_request_at
    now = time.monotonic()
    if _last_request_at is not None:
        delay = settings.NOMINATIM_MIN_INTERVAL_SECONDS - (now - _last_request_at)
        if delay > 0:
            time.sleep(delay)
    _last_request_at = time.monotonic()


def _fetch_nominatim(query, text):
    try:
        resp = requests.get(
            settings.NOMINATIM_URL,
            params={**query, "format": "json", "limit": 1},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodingServiceError(f"Geocoding service failed for '{text}': {exc}") from exc
    if not results:
        raise GeocodingError(f"Could not find a US location for '{text}'.")
    try:
        r = results[0]
        loc = Location(float(r["lat"]), float(r["lon"]), str(r.get("display_name", text)), "nominatim")
        if not math.isfinite(loc.lat) or not math.isfinite(loc.lon):
            raise ValueError("Invalid coordinates")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise GeocodingServiceError("Geocoding service returned incomplete location data. Please try again.") from exc
    return loc
