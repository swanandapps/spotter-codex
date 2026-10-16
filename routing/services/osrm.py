"""The single external call: driving route from the public OSRM server (free, no key)."""

import bisect
import itertools
import math
import time
from dataclasses import dataclass
from functools import cached_property

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: list  # [(lat, lon), ...]
    api_calls: int = 1
    segment_distances_miles: tuple = ()
    segment_durations_seconds: tuple = ()

    @cached_property
    def time_profile(self):
        """Scale segment estimates to the route totals (which include turn time)."""
        distances, durations = self.segment_distances_miles, self.segment_durations_seconds
        if not distances or len(distances) != len(durations) or sum(distances) <= 0 or sum(durations) <= 0:
            return None
        distance_scale = self.distance_miles / sum(distances)
        time_scale = self.duration_seconds / sum(durations)
        return (
            [0.0, *(d * distance_scale for d in itertools.accumulate(distances))],
            [0.0, *(t * time_scale for t in itertools.accumulate(durations))],
        )

    def time_at_mile(self, mile):
        mile = max(0.0, min(float(mile), self.distance_miles))
        if self.time_profile is None:
            return self.duration_seconds * mile / self.distance_miles if self.distance_miles else 0.0
        distances, times = self.time_profile
        right = min(bisect.bisect_right(distances, mile), len(distances) - 1)
        left = max(0, right - 1)
        span = distances[right] - distances[left]
        fraction = (mile - distances[left]) / span if span else 0.0
        return times[left] + fraction * (times[right] - times[left])


def fetch_routes(start, finish):
    """Driving routes between start/finish (lat, lon): the fastest first, then any
    alternatives OSRM finds (usually one), all from the same single HTTP request.

    One retry if the public server times out, can't be reached, or returns a 5xx
    (the brief allows two or three calls). 4xx responses are never retried."""
    path = f"{start[1]:.6f},{start[0]:.6f};{finish[1]:.6f},{finish[0]:.6f}"
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{path}"
    for attempt in (1, 2):
        try:
            resp = requests.get(
                url,
                params={"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "true",
                        "annotations": "distance,duration"},
                headers={"User-Agent": settings.HTTP_USER_AGENT},
                timeout=settings.HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            if attempt == 2:
                raise RoutingError(f"Routing service unreachable: {exc}") from exc
        else:
            if resp.status_code < 500 or attempt == 2:
                break
        time.sleep(0.5)

    try:
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except ValueError as exc:
        raise RoutingError('Routing service returned unreadable data. Please try again.') from exc
    if not isinstance(data, dict):
        raise RoutingError('Routing service returned an unexpected response.')
    if data.get("code") in ("NoRoute", "NoSegment"):
        raise RoutingError("There is no drivable route between these two places.", status=422)
    if resp.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(data.get("message") or f"Routing service returned HTTP {resp.status_code}")

    try:
        return [_parse_route(row, attempt) for row in data['routes']]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise RoutingError('Routing service returned incomplete route data. Please try again.') from exc


def _parse_route(row, api_calls):
    distance, duration = float(row['distance']), float(row['duration'])
    coordinates = [(float(lat), float(lon)) for lon, lat in row['geometry']['coordinates']]
    if not all(math.isfinite(v) and v >= 0 for v in (distance, duration)) or len(coordinates) < 2:
        raise ValueError('Invalid route totals or geometry')
    if any(not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)
           for lat, lon in coordinates):
        raise ValueError('Invalid route coordinates')
    distances, durations = [], []
    for leg in row.get('legs', []):
        annotation = leg.get('annotation', {})
        distances.extend(annotation.get('distance', []))
        durations.extend(annotation.get('duration', []))
    if (len(distances) != len(coordinates) - 1 or len(durations) != len(distances)
            or any(not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in distances + durations)):
        distances, durations = [], []
    return Route(distance / METERS_PER_MILE, duration, coordinates, api_calls,
                 tuple(d / METERS_PER_MILE for d in distances), tuple(durations))
