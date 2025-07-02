from django.urls import path

from .loading_views import laws_loading_cancel, laws_loading_monitor, laws_loading_start
from .views import answer, existing_search, index, search, source

app_name = "laws"
urlpatterns = [
    path("", index, name="index"),
    path("search/", search, name="search"),
    path("search/<str:query_uuid>", existing_search, name="existing_search"),
    path("answer/<str:query_uuid>", answer, name="answer"),
    path("source/<str:source_id>", source, name="source"),
    path("loading/monitor", laws_loading_monitor, name="loading_monitor"),
    path("loading/start", laws_loading_start, name="loading_start"),
    path("loading/cancel", laws_loading_cancel, name="loading_cancel"),
]
