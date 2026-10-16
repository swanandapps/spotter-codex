"""Data for a self-contained, printable trip brief; no tile or routing requests."""

import math


def diagram(plan):
    feature = next(f for f in plan['map']['features'] if f['properties']['kind'] == 'route')
    points = feature['geometry']['coordinates']
    latitude = sum(p[1] for p in points) / len(points)
    factor = math.cos(math.radians(latitude))
    projected = [(lon * factor, -lat) for lon, lat in points]
    min_x, max_x = min(p[0] for p in projected), max(p[0] for p in projected)
    min_y, max_y = min(p[1] for p in projected), max(p[1] for p in projected)
    scale = min(640 / max(max_x - min_x, .01), 220 / max(max_y - min_y, .01))
    mid_x, mid_y = (min_x + max_x) / 2, (min_y + max_y) / 2

    def xy(lon, lat):
        return {'x': round(360 + (lon * factor - mid_x) * scale, 1),
                'y': round(140 + (-lat - mid_y) * scale, 1)}

    route = [xy(*p) for p in points]
    return {
        'path': ' '.join(f"{'M' if i == 0 else 'L'}{p['x']},{p['y']}" for i, p in enumerate(route)),
        'start': route[0], 'finish': route[-1],
        'stops': [{**xy(s['station']['lon'], s['station']['lat']), 'number': s['stop_number']}
                  for s in plan['fuel_stops']],
    }


def duration(hours):
    minutes = round(hours * 60)
    return f'{minutes // 60}h {minutes % 60:02d}m'


def brief_context(plan):
    stops = plan['fuel_stops']
    rows = [{**leg, 'duration': duration(leg['driving_hours']),
             'stop': stops[leg['to_stop_number'] - 1] if leg['to_stop_number'] else None}
            for leg in plan['legs']]
    return {'plan': plan, 'diagram': diagram(plan), 'legs': rows, 'duration': duration(plan['route']['duration_hours'])}
