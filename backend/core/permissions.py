"""DRF permissions for subscription-gated access."""

from rest_framework import permissions

from core.models import Subscription


class SubscriptionRequired(permissions.BasePermission):
    """Allow access only to users with an active subscription.

    The user_id is extracted from the JWT by ExternalSiteJWTAuthentication
    and stored in request.auth. This permission checks the Subscription table.
    """

    message = "Active subscription required."

    def has_permission(self, request, view):
        user_id = request.auth
        if user_id is None:
            return False

        try:
            subscription = Subscription.objects.get(user_id=user_id)
        except Subscription.DoesNotExist:
            return False

        return subscription.is_valid


class IsAdminOrSubscriptionRequired(permissions.BasePermission):
    """Admin users bypass subscription check.

    Admin is only meaningful for Django session-auth (real User objects).
    JWT-authenticated requests have a string in request.user (the user id),
    so they never short-circuit here and always fall through to the
    subscription check below.
    """

    def has_permission(self, request, view):
        user = request.user if hasattr(request, "user") else None
        if user is not None and not isinstance(user, str) and user.is_staff:
            return True

        user_id = request.auth
        if user_id is None:
            return False

        try:
            subscription = Subscription.objects.get(user_id=user_id)
        except Subscription.DoesNotExist:
            return False

        return subscription.is_valid
