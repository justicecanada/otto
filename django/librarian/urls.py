from django.urls import path

from librarian.views import document_start, document_stop, modal, upload

app_name = "librarian"
urlpatterns = [
    path("modal/", modal, name="modal"),
    path("modal/<str:item_type>/", modal, name="modal_create"),
    path(
        "modal/<str:item_type>/from/<int:parent_id>/", modal, name="modal_create_from"
    ),
    path("modal/upload/to/<int:data_source_id>/", upload, name="upload"),
    path("modal/<str:item_type>/<int:item_id>/", modal, name="modal_existing_item"),
    path("document/<int:document_id>/start/", document_start, name="document_start"),
    path("document/<int:document_id>/stop/", document_stop, name="document_stop"),
]
