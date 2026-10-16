"""Find which fuel stations lie along a route, and how far along it they are."""

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .geo import EARTH_RADIUS_MILES, densify, haversine_miles


@dataclass(frozen=True)
class StationOnRoute:
    index: int  # row in the station arrays
    route_mile: float  # distance from origin along the route
    offset_miles: float  # straight-line distance from the route


def _unit_vectors(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def stations_along_route(route_coords, station_lat, station_lon, corridor_miles,
                         route_distance_miles=None, segment_distances_miles=None):
    """Return stations within corridor_miles of the route polyline.

    The route is densified to <=1 mile spacing, so the nearest route vertex is within
    0.5 mi of the true nearest point on the line. Vertices go into a KD-tree on the
    unit sphere; each station is one bounded nearest-neighbour query. A cross-country
    route (35k vertices) against 6.6k stations takes tens of milliseconds.
    """
    dense, cumulative = densify(route_coords, max_step_miles=1.0)
    if route_distance_miles is not None and cumulative[-1] > 0:
        if segment_distances_miles and len(segment_distances_miles) == len(route_coords) - 1 and sum(segment_distances_miles) > 0:
            vertices = np.asarray(route_coords)
            geometric = np.concatenate([[0.0], np.cumsum(haversine_miles(
                vertices[:-1, 0], vertices[:-1, 1], vertices[1:, 0], vertices[1:, 1]))])
            road = np.concatenate([[0.0], np.cumsum(segment_distances_miles)])
            road *= route_distance_miles / road[-1]
            cumulative = np.interp(cumulative, geometric, road)
        else:
            cumulative *= route_distance_miles / cumulative[-1]
    tree = cKDTree(_unit_vectors(dense[:, 0], dense[:, 1]))

    chord_limit = 2 * np.sin(corridor_miles / (2 * EARTH_RADIUS_MILES))
    chord, nearest = tree.query(
        _unit_vectors(station_lat, station_lon), k=1, distance_upper_bound=chord_limit
    )
    hits = np.nonzero(np.isfinite(chord))[0]
    offsets = 2 * EARTH_RADIUS_MILES * np.arcsin(chord[hits] / 2)
    return [
        StationOnRoute(int(i), float(cumulative[nearest[i]]), float(off))
        for i, off in zip(hits, offsets)
    ]
