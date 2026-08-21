"""Tests for subscription-gating permissions (core.permissions)."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.models import Subscription
from core.permissions import IsAdminOrSubscriptionRequired, SubscriptionRequired

pytestmark = pytest.mark.django_db

USER_ID = "site-user-42"


@pytest.fixture
def factory():
    return APIRequestFactory()


def make_request(user=None, auth=None):
    request = Request(APIRequestFactory().get("/api/v1/assets/"))
    if user is not None:
        request.user = user
    if auth is not None:
        request.auth = auth
    return request


def create_subscription(
    user_id=USER_ID,
    status=Subscription.Status.ACTIVE,
    expires_in=timedelta(days=30),
    **kwargs,
):
    return Subscription.objects.create(
        user_id=user_id,
        email="user@example.com",
        status=status,
        plan="monthly",
        starts_at=timezone.now(),
        expires_at=timezone.now() + expires_in if expires_in is not None else None,
        **kwargs,
    )


class TestSubscriptionRequired:
    def test_active_subscription_grants_access(self, factory):
        create_subscription()

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is True

    def test_expired_subscription_denies_access(self, factory):
        create_subscription(status=Subscription.Status.ACTIVE, expires_in=timedelta(days=-1))

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is False

    def test_expired_status_denies_access_even_with_future_expiry(self, factory):
        create_subscription(status=Subscription.Status.EXPIRED, expires_in=timedelta(days=30))

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is False

    def test_no_subscription_denies_access(self, factory):
        assert Subscription.objects.count() == 0

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is False

    def test_missing_auth_denies_access(self, factory):
        create_subscription(user_id="someone-else")

        assert SubscriptionRequired().has_permission(make_request(auth=None), None) is False

    def test_other_users_subscription_does_not_grant_access(self, factory):
        create_subscription(user_id="a-different-user")

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is False

    def test_none_expiry_denies_access(self, factory):
        create_subscription(expires_in=None)

        assert SubscriptionRequired().has_permission(make_request(auth=USER_ID), None) is False


class TestIsAdminOrSubscriptionRequired:
    def test_admin_bypasses_subscription_check(self, factory):
        User.objects.create_user(username="admin-user", password="pw", is_staff=True)
        admin = User.objects.get(username="admin-user")
        assert Subscription.objects.count() == 0

        request = make_request(user=admin, auth=None)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is True

    def test_admin_bypasses_even_with_expired_subscription(self, factory):
        User.objects.create_user(username="admin-user", password="pw", is_staff=True)
        admin = User.objects.get(username="admin-user")
        create_subscription(status=Subscription.Status.ACTIVE, expires_in=timedelta(days=-1))

        request = make_request(user=admin, auth=USER_ID)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is True

    def test_non_admin_with_active_subscription_is_allowed(self, factory):
        create_subscription()
        non_admin = AnonymousUser()

        request = make_request(user=non_admin, auth=USER_ID)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is True

    def test_non_admin_without_subscription_is_denied(self, factory):
        non_admin = AnonymousUser()

        request = make_request(user=non_admin, auth=USER_ID)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is False

    def test_non_admin_without_auth_is_denied(self, factory):
        create_subscription(user_id="someone-else")
        non_admin = AnonymousUser()

        request = make_request(user=non_admin, auth=None)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is False

    def test_non_admin_with_expired_subscription_is_denied(self, factory):
        create_subscription(expires_in=timedelta(days=-1))
        non_admin = AnonymousUser()

        request = make_request(user=non_admin, auth=USER_ID)

        assert IsAdminOrSubscriptionRequired().has_permission(request, None) is False
