from django.db import models


class FuelStation(models.Model):
    """A truck stop from the provided price list, geocoded offline to its city."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField(null=True)
    price = models.DecimalField(max_digits=8, decimal_places=5)
    lat = models.FloatField()
    lon = models.FloatField()

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"


class Place(models.Model):
    """US Census gazetteer place, used to resolve 'City, ST' inputs without an API call."""

    name = models.CharField(max_length=128)
    normalized = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    lat = models.FloatField()
    lon = models.FloatField()
    population = models.IntegerField(default=0)  # ranks autocomplete; 0 for CDPs

    class Meta:
        indexes = [models.Index(fields=["normalized", "state"])]

    def __str__(self):
        return f"{self.name}, {self.state}"
