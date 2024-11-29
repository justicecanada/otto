# django/otto/utils/db_wrapper.py

from django.db.backends.postgresql import base

from retrying import retry


class DatabaseWrapper(base.DatabaseWrapper):
    def get_new_connection(self, conn_params):
        return super().get_new_connection(conn_params)
