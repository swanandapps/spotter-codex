"""Small geometry helpers. Everything works in (lat, lon) degrees and miles."""

import json
import math
import re
from pathlib import Path

import numpy as np

EARTH_RADIUS_MILES = 3958.8



def haversine_miles(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance. Accepts scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def densify(coords, max_step_miles=1.0):
    """Insert points so no two consecutive vertices are more than max_step_miles apart.

    OSRM leaves long straight interstate stretches as a single segment; densifying lets
    a nearest-vertex search approximate distance-to-polyline within max_step/2.
    Returns (dense_coords Nx2, cumulative_miles N).
    """
    coords = np.asarray(coords, dtype=float)
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    pieces = np.maximum(1, np.ceil(seg / max_step_miles).astype(int))

    # Segment i contributes pieces[i] points at t = 1/n, 2/n, ..., 1 (vectorised).
    seg_idx = np.repeat(np.arange(len(pieces)), pieces)
    first = np.repeat(np.cumsum(pieces) - pieces, pieces)
    t = ((np.arange(seg_idx.size) - first + 1) / pieces[seg_idx])[:, None]
    a, b = coords[seg_idx], coords[seg_idx + 1]
    dense = np.vstack([coords[:1], a + (b - a) * t])

    step = haversine_miles(dense[:-1, 0], dense[:-1, 1], dense[1:, 0], dense[1:, 1])
    cumulative = np.concatenate([[0.0], np.cumsum(step)])
    return dense, cumulative


def thin(coords, min_step_miles=0.5):
    """Keep roughly one vertex per min_step_miles of route, plus both endpoints (for display)."""
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 3:
        return coords
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    bucket = np.floor(np.concatenate([[0.0], np.cumsum(seg)]) / min_step_miles)
    keep = np.concatenate([[True], np.diff(bucket) > 0])
    keep[-1] = True
    return coords[keep]


_BORDER = None


def _border():
    """US Census outline of the 50 states + DC: [(ring Nx2 lon/lat, bbox)], loaded once."""
    global _BORDER
    if _BORDER is None:
        path = Path(__file__).resolve().parents[2] / "data" / "us_border.json"
        rings = [np.asarray(r, dtype=float) for r in json.loads(path.read_text())]
        _BORDER = [(r, (r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max())) for r in rings]
    return _BORDER


def in_usa(lat, lon):
    """True if (lat, lon) is inside the US (50 states + DC), not just a box around it.

    Even-odd ray casting over the Census boundary rings, so Toronto, Tijuana and
    Windsor, ON are outside even though they sit within the old lat/lon box.
    """
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    inside = False
    for ring, (x0, y0, x1, y1) in _border():
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        xa, ya = ring[:-1, 0], ring[:-1, 1]
        xb, yb = ring[1:, 0], ring[1:, 1]
        crosses = (ya > lat) != (yb > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = xa + (lat - ya) * (xb - xa) / (yb - ya)
        inside ^= bool(np.count_nonzero(crosses & (lon < x_at)) % 2)
    return inside


_PLACE_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government"
    r"|urban county|city|town|village|borough|municipality|cdp|comunidad|zona urbana)$"
)
_ABBREVIATIONS = [(r"\bst\b\.?", "saint"), (r"\bste\b\.?", "sainte"), (r"\bft\b\.?", "fort"), (r"\bmt\b\.?", "mount")]


def normalize_place(name):
    """Canonical key for matching CSV city names against Census gazetteer names.

    Spaces are dropped so 'Mc Calla' == 'McCalla' and 'De Forest' == 'DeForest'.
    'Big Cabin town' -> 'bigcabin', 'East St. Louis' -> 'eastsaintlouis'.
    """
    s = name.lower().strip()
    s = re.sub(r"\(.*?\)", "", s).strip()
    for _ in range(2):
        s = _PLACE_SUFFIX.sub("", s).strip()
    for pattern, repl in _ABBREVIATIONS:
        s = re.sub(pattern, repl, s)
    return re.sub(r"[^a-z0-9]", "", s)


_DISPLAY_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government"
    r"|urban county|city|town|village|borough|CDP|municipality)$"
)


def display_place(gazetteer_name):
    """'Juneau city and borough' -> 'Juneau'; 'Indianapolis city (balance)' -> 'Indianapolis'."""
    return _DISPLAY_SUFFIX.sub("", re.sub(r"\s*\(balance\)", "", gazetteer_name))


def place_aliases(gazetteer_name):
    """All keys a gazetteer place should be findable under.

    Consolidated cities are listed as e.g. 'Nashville-Davidson metropolitan government
    (balance)' or 'Augusta-Richmond County consolidated government (balance)'; the
    station list just says 'Nashville' / 'Augusta', so also index the pre-hyphen part.
    """
    keys = {normalize_place(gazetteer_name)}
    head = re.sub(r"\(.*?\)", "", gazetteer_name).split("-")[0]
    if head.strip() and "-" in gazetteer_name:
        keys.add(normalize_place(head))
    keys.discard("")
    return keys
