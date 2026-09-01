"""Tests for ExternalSiteJWTAuthentication (core.auth)."""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.auth import ExternalSiteJWTAuthentication


VALID_PAYLOAD = {"user_id": "site-user-42", "email": "user@example.com"}


@pytest.fixture
def auth_backend():
    return ExternalSiteJWTAuthentication()


@pytest.fixture
def factory():
    return APIRequestFactory()


def make_request(factory, authorization=None):
    kwargs = {}
    if authorization is not None:
        kwargs["HTTP_AUTHORIZATION"] = authorization
    return Request(factory.get("/api/v1/assets/", **kwargs))


def encode_token(payload, key=None, algorithm=None):
    return jwt.encode(
        payload,
        key if key is not None else settings.JWT_SIGNING_KEY,
        algorithm=algorithm or settings.JWT_ALGORITHM,
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class TestValidToken:
    def test_valid_token_authenticates(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD)

        result = auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert result is not None
        user_id, auth = result
        assert user_id == "site-user-42"
        assert auth == "site-user-42"

    def test_lowercase_bearer_keyword_is_accepted(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD)

        result = auth_backend.authenticate(make_request(factory, f"bearer {token}"))

        assert result is not None
        assert result[0] == "site-user-42"

    def test_returned_auth_is_the_user_id_string(self, auth_backend, factory):
        payload = {"user_id": "u-abc", "role": "member", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        token = encode_token(payload)

        user, auth = auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert user == "u-abc"
        # request.auth must be the user_id string, not the raw JWT payload,
        # because the default SubscriptionRequired permission looks the
        # subscription up by request.auth. See core/permissions.
        assert auth == "u-abc"


class TestExpiredToken:
    def test_expired_token_raises_authentication_failed(self, auth_backend, factory):
        payload = {**VALID_PAYLOAD, "exp": datetime.now(timezone.utc) - timedelta(minutes=5)}
        token = encode_token(payload)

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert "expired" in str(exc_info.value.detail).lower()

    def test_just_expired_token_is_rejected(self, auth_backend, factory):
        payload = {**VALID_PAYLOAD, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}
        token = encode_token(payload)

        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))


class TestInvalidToken:
    def test_garbage_token_raises_authentication_failed(self, auth_backend, factory):
        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, "Bearer not-a-real-jwt"))

        assert "invalid token" in str(exc_info.value.detail).lower()

    def test_token_signed_with_wrong_key_raises_authentication_failed(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD, key="attacker-controlled-key")

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert "invalid token" in str(exc_info.value.detail).lower()

    def test_tampered_payload_raises_authentication_failed(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD)
        header, claims, signature = token.split(".")
        tampered_claims = _b64url(json.dumps({"user_id": "victim-user"}).encode())
        forged = f"{header}.{tampered_claims}.{signature}"

        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, f"Bearer {forged}"))

    def test_empty_token_raises_authentication_failed(self, auth_backend, factory):
        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, "Bearer "))


class TestMissingAuthorizationHeader:
    def test_no_authorization_header_returns_none(self, auth_backend, factory):
        result = auth_backend.authenticate(make_request(factory))

        assert result is None

    def test_non_bearer_scheme_returns_none(self, auth_backend, factory):
        result = auth_backend.authenticate(make_request(factory, "Basic dXNlcjpwYXNz"))

        assert result is None

    def test_other_scheme_returns_none(self, auth_backend, factory):
        result = auth_backend.authenticate(make_request(factory, "Token some-opaque-token"))

        assert result is None


class TestMalformedAuthorizationHeader:
    def test_keyword_only_no_token_raises(self, auth_backend, factory):
        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, "Bearer"))

        assert "invalid token header" in str(exc_info.value.detail).lower()

    def test_too_many_parts_raises(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD)

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {token} extra-part"))

        assert "invalid token header" in str(exc_info.value.detail).lower()


class TestWrongAlgorithm:
    def test_hs512_token_rejected_when_hs256_expected(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD, algorithm="HS512")

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert "invalid token" in str(exc_info.value.detail).lower()

    def test_alg_none_attack_is_rejected(self, auth_backend, factory):
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        claims = _b64url(json.dumps(VALID_PAYLOAD).encode())
        unsigned_token = f"{header}.{claims}."

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {unsigned_token}"))

        assert "invalid token" in str(exc_info.value.detail).lower()

    def test_rs256_token_rejected_when_hs256_expected(self, auth_backend, factory):
        token = encode_token(VALID_PAYLOAD, algorithm="HS384")

        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))


class TestMissingUserIdClaim:
    def test_payload_without_user_id_raises(self, auth_backend, factory):
        token = encode_token({"sub": "someone", "email": "user@example.com"})

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

        assert "missing user id" in str(exc_info.value.detail).lower()

    def test_empty_user_id_claim_raises(self, auth_backend, factory):
        token = encode_token({"user_id": ""})

        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))

    def test_none_user_id_claim_raises(self, auth_backend, factory):
        token = encode_token({"user_id": None})

        with pytest.raises(AuthenticationFailed):
            auth_backend.authenticate(make_request(factory, f"Bearer {token}"))
