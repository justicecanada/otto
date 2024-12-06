# django/otto/utils/db_wrapper.py

from django.db.backends.postgresql import base

from retrying import retry


class DatabaseWrapper(base.DatabaseWrapper):
    @retry(
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
    )
    def get_new_connection(self, conn_params):
        return super().get_new_connection(conn_params)
