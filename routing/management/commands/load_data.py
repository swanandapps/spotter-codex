"""Load the committed data files into the database. Fully offline, idempotent."""

import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation, Place

from ._places import load_place_index

DATA = Path(settings.BASE_DIR) / "data"


class Command(BaseCommand):
    help = "Load US places and geocoded fuel stations into the database"

    @transaction.atomic
    def handle(self, *args, **opts):
        places = load_place_index(DATA / "us_places.csv")
        Place.objects.all().delete()
        Place.objects.bulk_create(
            [
                Place(name=name, normalized=key, state=state, lat=lat, lon=lon, population=pop)
                for (key, state), (lat, lon, name, pop) in places.items()
            ],
            batch_size=2000,
        )

        with open(DATA / "fuel_stations_geocoded.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        FuelStation.objects.bulk_create(
            [
                FuelStation(
                    opis_id=int(r["opis_id"]),
                    name=r["name"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    rack_id=int(r["rack_id"]) if r["rack_id"] else None,
                    price=Decimal(r["price"]),
                    lat=float(r["lat"]),
                    lon=float(r["lon"]),
                )
                for r in rows
            ],
            batch_size=2000,
            update_conflicts=True,
            unique_fields=["opis_id"],
            update_fields=["name", "address", "city", "state", "rack_id", "price", "lat", "lon"],
        )
        # Keep IDs stable so saved trip links and station exclusions survive a price reload.
        FuelStation.objects.exclude(opis_id__in=[int(r["opis_id"]) for r in rows]).delete()
        self.stdout.write(
            self.style.SUCCESS(f"Loaded {Place.objects.count()} places, {FuelStation.objects.count()} stations")
        )
