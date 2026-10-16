import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# Django's default ("same-origin") strips the Referer from map tile requests, and the
# OpenStreetMap tile servers reject requests without one.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "spotter",
        "TIMEOUT": 60 * 60 * 24,
    }
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Route planning ---------------------------------------------------------
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
HTTP_USER_AGENT = "spotter-fuel-route-assessment/1.0"
HTTP_TIMEOUT_SECONDS = 15
ROUTING_CACHE_SECONDS = 60 * 60
GEOCODING_CACHE_SECONDS = 60 * 60 * 24 * 7
NOMINATIM_MIN_INTERVAL_SECONDS = 1.0

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
# Stations are geocoded to their city centroid, so we accept stations within this
# distance of the route line as "on route".
ROUTE_CORRIDOR_MILES = 10
# Optimiser objective is fuel cost + this many dollars per stop. 0 = cheapest fuel
# bill regardless of stop count; ~$10 removes "stop for 1 gallon" plans. Overridable
# per request with stop_penalty.
FUEL_STOP_PENALTY_USD = 10.0
# Fleets don't run tanks dry: plans never let fuel drop below this many miles of range.
FUEL_RESERVE_MILES = 50
# When OSRM offers alternate routes, they're compared on fuel cost + stop penalty +
# driving time valued at this rate, so a route can't win by saving a few dollars of
# diesel while adding hours.
DRIVER_TIME_USD_PER_HOUR = 30.0
