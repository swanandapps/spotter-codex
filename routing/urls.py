from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("route/map/", views.route_map, name="route-map"),
    path("route/brief/", views.route_brief, name="route-brief"),
    path("places/", views.place_suggestions, name="place-suggestions"),
    path("places/nearest/", views.nearest_place, name="nearest-place"),
]
