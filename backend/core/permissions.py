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
    """Admin users bypass subscription check."""

    def has_permission(self, request, view):
        if request.user and request.user.is_staff:
            return True

        user_id = request.auth
        if user_id is None:
            return False

        try:
            subscription = Subscription.objects.get(user_id=user_id)
        except Subscription.DoesNotExist:
            return False

        return subscription.is_valid
