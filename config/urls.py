from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/api/route/map/", permanent=False)),
    path("api/", include("routing.urls")),
]
