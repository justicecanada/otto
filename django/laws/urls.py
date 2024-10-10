from django.urls import path

from .views import answer, existing_search, index, search, source

app_name = "laws"
urlpatterns = [
    path("", index, name="index"),
    path("search/", search, name="search"),
    path("search/<str:query_uuid>", existing_search, name="existing_search"),
    path("answer/<str:query_uuid>", answer, name="answer"),
    path("source/<str:source_id>", source, name="source"),
]
