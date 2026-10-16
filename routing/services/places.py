"""Place lookups for the trip form: autocomplete and "which town is this pin near"."""

import threading

import numpy as np

from ..models import Place
from .geo import display_place, haversine_miles, normalize_place
from .geocode import US_STATES


def suggest(query, limit=8):
    """Places whose name starts with the query, biggest first: 'ch' -> Chicago, IL.

    Accepts 'chi', 'chicago, i' or 'chicago, il'. Aliases (Nashville for
    Nashville-Davidson) share a display name, so results are de-duplicated.
    """
    name, _, state = query.partition(",")
    key, state = normalize_place(name), state.strip().upper()
    if not key:
        return []
    rows = Place.objects.filter(normalized__startswith=key)
    if state:
        rows = rows.filter(state__startswith=state)
    seen, out = set(), []
    for p in rows.order_by("-population", "name")[: limit * 3]:
        label = f"{display_place(p.name)}, {p.state}"
        if label not in seen and p.state in US_STATES:
            seen.add(label)
            out.append({"label": label, "lat": p.lat, "lon": p.lon})
        if len(out) == limit:
            break
    return out


class _PlaceIndex:
    _lock = threading.Lock()
    _arrays = None

    @classmethod
    def arrays(cls):
        if cls._arrays is None:
            with cls._lock:
                if cls._arrays is None:
                    rows = list(Place.objects.values_list("name", "state", "lat", "lon", "population"))
                    cls._arrays = (
                        rows,
                        np.array([r[2] for r in rows]),
                        np.array([r[3] for r in rows]),
                    )
        return cls._arrays


def nearest(lat, lon):
    """Name a dropped pin the way a driver would: the biggest town close by.

    The strictly nearest Census place is often an obscure CDP ('Preston Heights');
    among places within 3x that distance (at least 5 mi), take the most populous.
    Returns e.g. {'label': 'Joliet, IL', 'miles': 3.1}.
    """
    rows, lats, lons = _PlaceIndex.arrays()
    if not rows:
        return None
    d = haversine_miles(lat, lon, lats, lons)
    close = np.nonzero(d <= max(5.0, 3 * d.min()))[0]
    i = int(max(close, key=lambda k: (rows[k][4], -d[k])))
    name, state = rows[i][0], rows[i][1]
    return {"label": f"{display_place(name)}, {state}", "miles": round(float(d[i]), 1)}
