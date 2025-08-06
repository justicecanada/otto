from django.urls import path

from . import views

urlpatterns = [
    path("clear/", views.clear_search_history, name="clear_history"),
    path("<int:search_id>/", views.view_search, name="view_search"),
]
