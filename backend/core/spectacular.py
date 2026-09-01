"""drf-spectacular extensions for the custom JWT authentication backend.

Without this, drf-spectacular can't map ExternalSiteJWTAuthentication to an
OpenAPI security scheme and warns on every schema generation. Registering it
lets the Swagger UI advertise the Bearer-token security requirement.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object

from core.auth import ExternalSiteJWTAuthentication


class ExternalSiteJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = ExternalSiteJWTAuthentication
    name = "externalSiteJWT"

    def get_security_definition(self, auto_schema):
        return build_bearer_security_scheme_object(
            header_name="Authorization", token_prefix="Bearer", bearer_format="JWT"
        )
