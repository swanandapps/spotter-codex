"""Reuse road geometry when fuel, price data, or stop choices change.

Only successful results are cached. Requests for the same uncached route share
one in-flight lookup within this process; unrelated routes do not block each other.
"""

import dataclasses
import hashlib
import threading
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache

from . import osrm


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    routes: list | None = None
    error: Exception | None = None


_lock = threading.Lock()
_flights = {}


def get_routes(start, finish):
    raw = f"road-v2:{settings.OSRM_BASE_URL}:{start!r}:{finish!r}"
    key = 'road:' + hashlib.sha256(raw.encode()).hexdigest()
    with _lock:
        cached = cache.get(key)
        if cached is not None:
            return _reused(cached), True
        flight = _flights.get(key)
        owner = flight is None
        if owner:
            flight = _flights[key] = _Flight()

    if not owner:
        if not flight.done.wait(settings.HTTP_TIMEOUT_SECONDS * 2 + 5):
            raise osrm.RoutingError('The route lookup is taking too long. Please try again.', status=503)
        if flight.error is not None:
            raise osrm.RoutingError(str(flight.error), status=getattr(flight.error, 'status', 502))
        return _reused(flight.routes), True

    try:
        flight.routes = osrm.fetch_routes(start, finish)
        cache.set(key, flight.routes, timeout=settings.ROUTING_CACHE_SECONDS)
        return flight.routes, False
    except Exception as exc:
        flight.error = exc
        raise
    finally:
        with _lock:
            _flights.pop(key, None)
            flight.done.set()


def _reused(routes):
    # Report calls for this request, not the request which populated the cache.
    return [dataclasses.replace(route, api_calls=0) for route in routes]
