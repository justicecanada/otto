from django.urls import path

from .views import advanced_search_form, answer, index, search, source

app_name = "laws"
urlpatterns = [
    path("", index, name="index"),
    path("search/", search, name="search"),
    path("answer/", answer, name="answer"),
    path("source/<source_id>", source, name="source"),
    path("advanced_search_form/", advanced_search_form, name="advanced_search_form"),
]
