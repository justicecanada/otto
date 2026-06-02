from rest_framework.pagination import LimitOffsetPagination


class OttoLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 100
    max_limit = 500
