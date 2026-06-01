from drf_spectacular.extensions import OpenApiAuthenticationExtension


class OttoMachineTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "otto.api.authentication.OttoMachineTokenAuthentication"
    name = "ottoBearerToken"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "OttoApiToken",
            "description": "Managed Otto machine-client bearer token.",
        }


class OttoSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "otto.api.authentication.OttoSessionAuthentication"
    name = "ottoSession"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "Existing Otto browser session.",
        }
