import csv

from routing.services.geo import normalize_place, place_aliases


def read_places(path):
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield r["name"], r["state"], float(r["lat"]), float(r["lon"]), int(r.get("population") or 0)


def load_place_index(path):
    """{(normalized_name, state): (lat, lon, name, population)}.

    Exact names win over pre-hyphen aliases, so a real 'Winston, XX' is never
    shadowed by 'Winston-Salem'. Where a state has two places with the same name,
    the more populous one wins.
    """
    exact, alias = {}, {}
    for name, state, lat, lon, pop in read_places(path):
        primary = normalize_place(name)
        if (primary, state) not in exact or pop > exact[(primary, state)][3]:
            exact[(primary, state)] = (lat, lon, name, pop)
        for key in place_aliases(name) - {primary}:
            alias.setdefault((key, state), (lat, lon, name, pop))
    return {**alias, **exact}
