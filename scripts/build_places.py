"""One-off: build data/us_places.csv from Census files.

Sources:
  gazetteer   https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip
  population  https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv
              (incorporated places only; CDPs get population 0)

Population is only used to rank autocomplete suggestions, so "Ch" offers Chicago first.
Usage: python scripts/build_places.py path/to/2024_Gaz_place_national.txt path/to/sub-est2023.csv
"""
import csv
import sys
from pathlib import Path

gazetteer, population = Path(sys.argv[1]), Path(sys.argv[2])
out = Path(__file__).resolve().parent.parent / "data" / "us_places.csv"

pop_by_geoid = {}
with population.open(encoding="latin-1") as f:
    for row in csv.DictReader(f):
        if row["SUMLEV"] in ("162", "170"):
            pop_by_geoid[row["STATE"] + row["PLACE"]] = int(row["POPESTIMATE2023"])

with gazetteer.open(encoding="utf-8") as f, out.open("w", newline="", encoding="utf-8") as g:
    reader = csv.DictReader(f, delimiter="\t")
    reader.fieldnames = [c.strip() for c in reader.fieldnames]
    w = csv.writer(g)
    w.writerow(["state", "name", "lat", "lon", "population"])
    n = matched = 0
    for row in reader:
        pop = pop_by_geoid.get(row["GEOID"].strip(), 0)
        matched += pop > 0
        w.writerow([row["USPS"], row["NAME"].strip(), row["INTPTLAT"].strip(), row["INTPTLONG"].strip(), pop])
        n += 1
print(f"wrote {n} places ({matched} with population) to {out}")
