import math
import time
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView

from .services import places
from .services.geo import in_usa
from .services.planner import PlanError, plan_trip
from .services.brief import brief_context


class FiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            self.fail("invalid")
        return value


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST', an address, or 'lat,lon'")
    finish = serializers.CharField(max_length=200, help_text="'City, ST', an address, or 'lat,lon'")
    stop_penalty = FiniteFloatField(
        required=False, min_value=0, max_value=1000,
        help_text="USD cost attributed to each stop; set this and driver_hourly_cost to 0 for fuel cost only",
    )
    start_fuel_gallons = FiniteFloatField(
        required=False, min_value=0, max_value=settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG,
        help_text="Fuel in the tank at departure (from the ELD); defaults to a full tank",
    )
    driver_hourly_cost = FiniteFloatField(
        required=False, min_value=0, max_value=1000,
        help_text="USD per driving hour used to compare routes; defaults to 30, use 0 to ignore driving time",
    )
    route_option = serializers.IntegerField(
        required=False, min_value=0, max_value=5,
        help_text="Show the fuel plan for this entry of route_options instead of the recommended route",
    )
    avoid_station_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=20,
        help_text="Exclude up to 20 station IDs. In GET requests repeat avoid_station_ids for each ID.",
    )


class RoutePlanView(APIView):
    """Plan a US trip with the cheapest fuel stops.

    POST {"start": "New York, NY", "finish": "Los Angeles, CA"}
    or   GET ?start=New York, NY&finish=Los Angeles, CA
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, data):
        serializer = RouteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        started = time.perf_counter()
        try:
            result = plan_trip(**serializer.validated_data)
        except PlanError as exc:
            return Response({"error": str(exc)}, status=exc.status)

        result["map_url"] = request.build_absolute_uri("/api/route/map/") + "?" + urlencode(serializer.validated_data, doseq=True)
        result["meta"] = {**result["meta"], "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
        return Response(result, headers={"Cache-Control": "private, no-store"})


def route_map(request):
    """MapLibre planner drawing the same plan, cached after a JSON request."""
    form = {key: request.GET.get(key, "") for key in (
        "start", "finish", "start_fuel_gallons", "stop_penalty", "driver_hourly_cost", "route_option",
    )}
    form["avoid_station_ids"] = request.GET.getlist("avoid_station_ids")
    defaults = {
        "start_fuel_gallons": settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG,
        "stop_penalty": settings.FUEL_STOP_PENALTY_USD,
        "driver_hourly_cost": settings.DRIVER_TIME_USD_PER_HOUR,
    }
    for key, value in defaults.items():
        if form[key] == "":
            form[key] = value
    context = {"form": form, "vehicle": {
        "tank": settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG,
        "mpg": settings.VEHICLE_MPG, "range": settings.VEHICLE_RANGE_MILES,
        "reserve": settings.FUEL_RESERVE_MILES / settings.VEHICLE_MPG,
    }}
    if not form["start"] and not form["finish"]:
        return render(request, "routing/map.html", context)
    query = request.GET.copy()
    for key in list(query):
        if not any(query.getlist(key)):
            del query[key]
    serializer = RouteRequestSerializer(data=query)
    if not serializer.is_valid():
        if "start" in serializer.errors or "finish" in serializer.errors:
            error = "Enter both a start and a finish, for example Dallas, TX."
        elif "start_fuel_gallons" in serializer.errors:
            error = f"Fuel in the tank must be between 0 and {settings.VEHICLE_RANGE_MILES // settings.VEHICLE_MPG} gallons."
        elif "stop_penalty" in serializer.errors or "driver_hourly_cost" in serializer.errors:
            error = "Stop cost and driving cost must be finite numbers between $0 and $1,000."
        elif "avoid_station_ids" in serializer.errors:
            error = "Exclude up to 20 stations using their numeric station IDs."
        else:
            error = "Choose a valid route option."
        return render(request, "routing/map.html", {**context, "error": error, "errors": serializer.errors}, status=400)
    started = time.perf_counter()
    try:
        result = plan_trip(**serializer.validated_data)
    except PlanError as exc:
        return render(request, "routing/map.html", {**context, "error": str(exc)}, status=exc.status)
    result["meta"] = {**result["meta"], "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    return render(request, "routing/map.html", {**context, "plan": result})


@api_view(["GET"])
def route_brief(request):
    """Printable and downloadable HTML using the same cached API plan."""
    serializer = RouteRequestSerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    try:
        result = plan_trip(**serializer.validated_data)
    except PlanError as exc:
        return Response({"error": str(exc)}, status=exc.status)
    query = urlencode(serializer.validated_data, doseq=True)
    context = {**brief_context(result), 'created_at': timezone.now(),
               'download_url': '?'+query+'&download=1',
               'map_url': request.build_absolute_uri('/api/route/map/')+'?'+query,
               'downloaded': request.query_params.get('download') == '1'}
    response = HttpResponse(render_to_string('routing/brief.html', context), content_type='text/html; charset=utf-8')
    if context['downloaded']:
        response['Content-Disposition'] = 'attachment; filename="spotter-trip-brief.html"'
    response['Cache-Control'] = 'private, no-store'
    return response


@api_view(["GET"])
def place_suggestions(request):
    """Autocomplete for the trip form: GET /api/places/?q=chi -> [{label, lat, lon}, ...]."""
    query = request.query_params.get("q", "")[:60]
    return Response(places.suggest(query))


@api_view(["GET"])
def nearest_place(request):
    """Name a dropped pin: GET /api/places/nearest/?lat=41.5&lon=-88.1 -> {label, miles}."""
    try:
        lat, lon = float(request.query_params["lat"]), float(request.query_params["lon"])
    except (KeyError, ValueError):
        return Response({"error": "Pass numeric lat and lon."}, status=400)
    if not in_usa(lat, lon):
        return Response({"error": "That point is outside the USA (50 states and DC)."}, status=400)
    return Response(places.nearest(lat, lon) or {})
