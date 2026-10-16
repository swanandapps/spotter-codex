"""One-off: convert the Census US boundary shapefile into data/us_border.json.

Source: https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_nation_5m.zip
Keeps the 50 states + DC (drops Puerto Rico and the Pacific territories), so "within
the USA" means inside this outline rather than inside a rough bounding box.
Reads the .shp polygon records directly (format is simple; no GIS dependency).
Usage: python scripts/build_us_border.py path/to/cb_2023_us_nation_5m.shp
"""
import json
import struct
import sys
from pathlib import Path


def polygons(shp_path):
    data = Path(shp_path).read_bytes()
    pos = 100  # file header
    while pos < len(data):
        _, length = struct.unpack(">ii", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length * 2]
        pos += 8 + length * 2
        if struct.unpack("<i", body[:4])[0] != 5:  # 5 = polygon
            continue
        num_parts, num_points = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{num_parts}i", body[44:44 + 4 * num_parts])) + [num_points]
        pts_at = 44 + 4 * num_parts
        points = struct.unpack(f"<{2 * num_points}d", body[pts_at:pts_at + 16 * num_points])
        for a, b in zip(parts, parts[1:]):
            yield [(points[2 * i], points[2 * i + 1]) for i in range(a, b)]


def in_states(ring):
    lon = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    if lon > 0:  # Guam, Northern Mariana Islands (east of the antimeridian)
        return False
    if lat < 20 and lon > -150:  # Puerto Rico, US Virgin Islands, American Samoa
        return False
    return True


rings = [[[round(x, 4), round(y, 4)] for x, y in ring] for ring in polygons(sys.argv[1]) if in_states(ring)]
out = Path(__file__).resolve().parent.parent / "data" / "us_border.json"
out.write_text(json.dumps(rings, separators=(",", ":")))
print(f"wrote {len(rings)} rings, {sum(map(len, rings))} points to {out}")
