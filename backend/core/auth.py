"""
JWT authentication backend that validates tokens signed by the main site.

The main site signs JWTs with a shared secret or RSA key. This backend
decodes and validates them, extracting the user_id for subscription lookups.
"""

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions
from rest_framework.request import Request

from core.models import Subscription


class ExternalSiteJWTAuthentication(authentication.BaseAuthentication):
    """Authenticate requests using JWTs from the main site."""

    keyword = "Bearer"

    def authenticate(self, request: Request):
        auth_header = authentication.get_authorization_header(request).split()

        if not auth_header or auth_header[0].lower() != self.keyword.lower().encode():
            return None

        if len(auth_header) != 2:
            raise exceptions.AuthenticationFailed("Invalid token header. Use 'Bearer <token>'.")

        token = auth_header[1].decode("utf-8")

        try:
            payload = jwt.decode(
                token,
                settings.JWT_SIGNING_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Token has expired.")
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("Invalid token.")

        user_id = payload.get(settings.JWT_USER_ID_FIELD, "")
        if not user_id:
            raise exceptions.AuthenticationFailed("Token missing user ID.")

        return (user_id, payload)


class SubscriptionRequiredPermission:
    """Permission that checks the user has an active subscription."""

    def __call__(self, request):
        user_id = authentication.get_authorization_header(request)
        # user_id is set by the authentication backend
        pass

    def has_permission(self, request, view):
        user_id = getattr(request, "auth", None)
        if user_id is None:
            return False

        try:
            subscription = Subscription.objects.get(user_id=user_id)
        except Subscription.DoesNotExist:
            return False

        return subscription.is_valid
