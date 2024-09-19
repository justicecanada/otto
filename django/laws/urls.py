from django.urls import path

from .views import answer, index, search, source

app_name = "laws"
urlpatterns = [
    path("", index, name="index"),
    path("search/", search, name="search"),
    path("answer/", answer, name="answer"),
    path("source/<source_id>", source, name="source"),
]
