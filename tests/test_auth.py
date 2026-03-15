"""
Tests for the Auth module — JWT silent auth, HTTP verification, and caching.
"""

import pytest
import time
import httpx
from unittest.mock import AsyncMock, patch
from jose import jwt

from auth.token_verify import (
    verify_token,
    VerifiedUser,
    clear_cache,
    _decode_jwt,
    JWTVerificationError,
)


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Clear token cache before each test."""
    clear_cache()
    yield
    clear_cache()


MOCK_USER_RESPONSE = {
    "user_id": 42,
    "username": "john.doe",
    "role": "HR_Manager",
    "department": "Human Resources",
    "permissions": ["employees", "leave_requests", "attendance"],
}

# ── JWT test helpers ─────────────────────────────────────

JWT_SECRET = "test-secret-key-for-unit-tests"
JWT_ALGORITHM = "HS256"


def _make_jwt(payload: dict, secret: str = JWT_SECRET) -> str:
    """Create a signed JWT for testing."""
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _valid_jwt_payload() -> dict:
    return {
        "user_id": 42,
        "username": "john.doe",
        "role": "HR_Manager",
        "department": "Human Resources",
        "permissions": ["employees", "leave_requests", "attendance"],
        "exp": int(time.time()) + 3600,  # Expires in 1 hour
    }


# ═════════════════════════════════════════════════════════
# JWT Mode Tests
# ═════════════════════════════════════════════════════════

class TestJWTDecode:

    @patch("auth.token_verify.settings")
    def test_valid_jwt_returns_user(self, mock_settings):
        mock_settings.jwt_secret_or_public_key = JWT_SECRET
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = ""
        mock_settings.jwt_issuer = ""

        token = _make_jwt(_valid_jwt_payload())
        user = _decode_jwt(token)

        assert isinstance(user, VerifiedUser)
        assert user.user_id == 42
        assert user.username == "john.doe"
        assert user.role == "HR_Manager"
        assert user.department == "Human Resources"
        assert "employees" in user.permissions

    @patch("auth.token_verify.settings")
    def test_expired_jwt_raises(self, mock_settings):
        mock_settings.jwt_secret_or_public_key = JWT_SECRET
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = ""
        mock_settings.jwt_issuer = ""

        payload = _valid_jwt_payload()
        payload["exp"] = int(time.time()) - 100  # Already expired

        token = _make_jwt(payload)
        with pytest.raises(JWTVerificationError, match="JWT decode failed"):
            _decode_jwt(token)

    @patch("auth.token_verify.settings")
    def test_wrong_secret_raises(self, mock_settings):
        mock_settings.jwt_secret_or_public_key = "wrong-secret"
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = ""
        mock_settings.jwt_issuer = ""

        token = _make_jwt(_valid_jwt_payload(), secret=JWT_SECRET)
        with pytest.raises(JWTVerificationError):
            _decode_jwt(token)

    @patch("auth.token_verify.settings")
    def test_missing_role_raises(self, mock_settings):
        mock_settings.jwt_secret_or_public_key = JWT_SECRET
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = ""
        mock_settings.jwt_issuer = ""

        payload = _valid_jwt_payload()
        del payload["role"]

        token = _make_jwt(payload)
        with pytest.raises(JWTVerificationError, match="missing 'role'"):
            _decode_jwt(token)

    @patch("auth.token_verify.settings")
    def test_sub_claim_used_as_user_id(self, mock_settings):
        """JWT with 'sub' instead of 'user_id' should work."""
        mock_settings.jwt_secret_or_public_key = JWT_SECRET
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = ""
        mock_settings.jwt_issuer = ""

        payload = _valid_jwt_payload()
        del payload["user_id"]
        payload["sub"] = "99"

        token = _make_jwt(payload)
        user = _decode_jwt(token)
        assert user.user_id == 99

    @patch("auth.token_verify.settings")
    def test_no_secret_configured_raises(self, mock_settings):
        mock_settings.jwt_secret_or_public_key = ""
        mock_settings.jwt_algorithm = JWT_ALGORITHM

        with pytest.raises(JWTVerificationError, match="not set"):
            _decode_jwt("any-token")

    @patch("auth.token_verify.settings")
    def test_audience_validation(self, mock_settings):
        """JWT with wrong audience should fail."""
        mock_settings.jwt_secret_or_public_key = JWT_SECRET
        mock_settings.jwt_algorithm = JWT_ALGORITHM
        mock_settings.jwt_audience = "atl-ai"
        mock_settings.jwt_issuer = ""

        payload = _valid_jwt_payload()
        payload["aud"] = "wrong-audience"

        token = _make_jwt(payload)
        with pytest.raises(JWTVerificationError):
            _decode_jwt(token)


# ═════════════════════════════════════════════════════════
# HTTP Mode Tests
# ═════════════════════════════════════════════════════════

class TestHTTPVerification:

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        mock_response = httpx.Response(
            200,
            json=MOCK_USER_RESPONSE,
            request=httpx.Request("GET", "http://test/verify-user"),
        )

        with patch("auth.token_verify.settings") as mock_settings:
            mock_settings.auth_mode = "http"
            mock_settings.main_app_base_url = "http://localhost:7000"
            mock_settings.main_app_verify_endpoint = "/verify-user"
            mock_settings.token_cache_ttl_seconds = 300

            with patch("auth.token_verify.httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.get = AsyncMock(return_value=mock_response)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                user = await verify_token("valid-token-123")

                assert isinstance(user, VerifiedUser)
                assert user.user_id == 42
                assert user.role == "HR_Manager"

    @pytest.mark.asyncio
    async def test_invalid_token_raises(self):
        mock_response = httpx.Response(401, json={"error": "Unauthorized"})
        mock_response.request = httpx.Request("GET", "http://test/verify-user")

        with patch("auth.token_verify.settings") as mock_settings:
            mock_settings.auth_mode = "http"
            mock_settings.main_app_base_url = "http://localhost:7000"
            mock_settings.main_app_verify_endpoint = "/verify-user"
            mock_settings.token_cache_ttl_seconds = 300

            with patch("auth.token_verify.httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.get = AsyncMock(return_value=mock_response)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                with pytest.raises(httpx.HTTPStatusError):
                    await verify_token("invalid-token")

    @pytest.mark.asyncio
    async def test_unreachable_main_app_raises(self):
        with patch("auth.token_verify.settings") as mock_settings:
            mock_settings.auth_mode = "http"
            mock_settings.main_app_base_url = "http://localhost:7000"
            mock_settings.main_app_verify_endpoint = "/verify-user"
            mock_settings.token_cache_ttl_seconds = 300

            with patch("auth.token_verify.httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.get = AsyncMock(
                    side_effect=httpx.ConnectError("Connection refused")
                )
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                with pytest.raises(ConnectionError, match="Cannot reach Main App"):
                    await verify_token("some-token")


# ═════════════════════════════════════════════════════════
# Cache Tests
# ═════════════════════════════════════════════════════════

class TestTokenCache:

    @pytest.mark.asyncio
    async def test_cached_token_no_second_call(self):
        mock_response = httpx.Response(
            200,
            json=MOCK_USER_RESPONSE,
            request=httpx.Request("GET", "http://test/verify-user"),
        )

        with patch("auth.token_verify.settings") as mock_settings:
            mock_settings.auth_mode = "http"
            mock_settings.main_app_base_url = "http://localhost:7000"
            mock_settings.main_app_verify_endpoint = "/verify-user"
            mock_settings.token_cache_ttl_seconds = 300

            with patch("auth.token_verify.httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.get = AsyncMock(return_value=mock_response)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                user1 = await verify_token("cached-token")
                assert user1.user_id == 42

                user2 = await verify_token("cached-token")
                assert user2.user_id == 42

                # Only 1 HTTP client creation (cache hit on second call)
                assert MockClient.call_count == 1
