from django.contrib import admin

from .models import DataSource, Document, Library

admin.site.register(Library)
admin.site.register(DataSource)
admin.site.register(Document)
