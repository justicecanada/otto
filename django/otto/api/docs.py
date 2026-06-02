from drf_spectacular.renderers import OpenApiJsonRenderer
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


class OttoApiSchemaView(SpectacularAPIView):
    renderer_classes = [OpenApiJsonRenderer]


class OttoApiSwaggerView(SpectacularSwaggerView):
    pass


class OttoApiRedocView(SpectacularRedocView):
    pass
