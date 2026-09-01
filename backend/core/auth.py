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

        # Return the user_id string as BOTH the request.user and request.auth
        # objects. The permissions layer (core.permissions) reads the user id
        # off request.auth — see SubscriptionRequired's docstring: "The user_id
        # is extracted from the JWT by ExternalSiteJWTAuthentication and stored
        # in request.auth". So request.auth must be the user_id string, not the
        # raw JWT payload dict. (The decoded claims are used for signature
        # validation above and discarded here.)
        self._record_usage(user_id)
        return (user_id, user_id)

    @staticmethod
    def _record_usage(user_id):
        """Increment the caller's daily API counter (B2B per 1,000-call
        billing, Feature 7). Runs on every authenticated request and is
        best-effort — an accounting failure must never reject a valid call.
        """
        try:
            from django.db.models import F
            from django.utils import timezone
            from core.models import ApiUsage
            today = timezone.localdate()
            # get_or_create (NOT update_or_create with a call_count default):
            # update_or_create would reset call_count to the default on every
            # hit via its update path, so consecutive requests would never
            # accumulate. get_or_create only creates; the create takes the
            # model's default call_count=0.
            obj, _ = ApiUsage.objects.get_or_create(user_id=user_id, date=today)
            ApiUsage.objects.filter(pk=obj.pk).update(call_count=F("call_count") + 1)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to record API usage for user=%s", user_id)


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
