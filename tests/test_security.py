"""Security tests for authentication and authorisation logic."""

import hashlib
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.cross_check.main as main_module
from src.cross_check.main import _is_email_domain_allowed, app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# _is_email_domain_allowed — pure function, no mocking needed
# ---------------------------------------------------------------------------


class TestEmailDomainAllowlist:
    def test_empty_allowlist_permits_any_email(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", []):
            assert _is_email_domain_allowed("anyone@anything.com")

    def test_exact_domain_match(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert _is_email_domain_allowed("user@example.com")

    def test_exact_entry_also_permits_subdomain(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert _is_email_domain_allowed("user@sub.example.com")

    def test_at_prefix_exact_only(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["@example.com"]):
            assert _is_email_domain_allowed("user@example.com")

    def test_at_prefix_blocks_subdomain(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["@example.com"]):
            assert not _is_email_domain_allowed("user@sub.example.com")

    def test_subdomain_not_allowed_by_unrelated_entry(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert not _is_email_domain_allowed("user@notexample.com")

    def test_partial_suffix_not_allowed(self):
        # 'evil-example.com' must not match allowed 'example.com'
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert not _is_email_domain_allowed("user@evil-example.com")

    def test_multiple_domains_first_match(self):
        with patch.object(
            main_module, "ALLOWED_EMAIL_DOMAINS", ["alpha.com", "beta.com"]
        ):
            assert _is_email_domain_allowed("user@alpha.com")
            assert _is_email_domain_allowed("user@beta.com")

    def test_multiple_domains_no_match(self):
        with patch.object(
            main_module, "ALLOWED_EMAIL_DOMAINS", ["alpha.com", "beta.com"]
        ):
            assert not _is_email_domain_allowed("user@gamma.com")

    def test_case_insensitive(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert _is_email_domain_allowed("User@EXAMPLE.COM")

    def test_email_with_multiple_at_signs_uses_rightmost_domain(self):
        # e.g. "attacker@evil.com@allowed.com" — domain is 'allowed.com'
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["@allowed.com"]):
            assert _is_email_domain_allowed("attacker@evil.com@allowed.com")

    def test_email_without_at_sign_denied(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert not _is_email_domain_allowed("nodomain")

    def test_empty_email_denied(self):
        with patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", ["example.com"]):
            assert not _is_email_domain_allowed("")


# ---------------------------------------------------------------------------
# auth_middleware — Clerk mode
# ---------------------------------------------------------------------------

VALID_CLAIMS = {"sub": "user_123", "email": "user@example.com"}


class TestAuthMiddlewareClerkMode:
    def _clerk_patches(self, claims=VALID_CLAIMS, allowed_domains=None):
        """Return a stack of context managers that simulate Clerk mode."""
        return [
            patch.object(main_module, "CLERK_ENABLED", True),
            patch.object(main_module, "PROTOTYPE_PASSWORD_ENABLED", False),
            patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", allowed_domains or []),
            patch.object(main_module, "_decode_clerk_token", return_value=claims),
        ]

    def _apply(self, patches):
        for p in patches:
            p.start()
        return patches

    def _stop(self, patches):
        for p in patches:
            p.stop()

    def test_no_auth_header_returns_401(self):
        patches = self._apply(self._clerk_patches())
        try:
            res = client.get("/api/session")
            assert res.status_code == 401
        finally:
            self._stop(patches)

    def test_non_bearer_token_returns_401(self):
        patches = self._apply(self._clerk_patches())
        try:
            res = client.get("/api/session", headers={"Authorization": "Basic abc"})
            assert res.status_code == 401
        finally:
            self._stop(patches)

    def test_invalid_jwt_returns_401(self):
        patches = self._apply(self._clerk_patches(claims=None))
        try:
            res = client.get(
                "/api/session", headers={"Authorization": "Bearer badtoken"}
            )
            assert res.status_code == 401
        finally:
            self._stop(patches)

    def test_valid_jwt_no_domain_restriction_passes(self):
        patches = self._apply(self._clerk_patches(allowed_domains=[]))
        try:
            res = client.get(
                "/api/session", headers={"Authorization": "Bearer validtoken"}
            )
            # 401 would mean auth failed; anything else means middleware passed it through
            assert res.status_code != 401
            assert res.status_code != 403
        finally:
            self._stop(patches)

    def test_valid_jwt_allowed_domain_passes(self):
        patches = self._apply(
            self._clerk_patches(
                claims={"sub": "u1", "email": "user@example.com"},
                allowed_domains=["example.com"],
            )
        )
        try:
            res = client.get(
                "/api/session", headers={"Authorization": "Bearer validtoken"}
            )
            assert res.status_code not in (401, 403)
        finally:
            self._stop(patches)

    def test_valid_jwt_blocked_domain_returns_403(self):
        patches = self._apply(
            self._clerk_patches(
                claims={"sub": "u1", "email": "user@blocked.com"},
                allowed_domains=["example.com"],
            )
        )
        try:
            res = client.get(
                "/api/session", headers={"Authorization": "Bearer validtoken"}
            )
            assert res.status_code == 403
        finally:
            self._stop(patches)

    def test_valid_jwt_missing_email_claim_returns_403_when_domains_set(self):
        # Fail closed: email not in JWT → deny when ALLOWED_EMAIL_DOMAINS is set
        patches = self._apply(
            self._clerk_patches(
                claims={"sub": "u1"},  # no email claim
                allowed_domains=["example.com"],
            )
        )
        try:
            res = client.get(
                "/api/session", headers={"Authorization": "Bearer validtoken"}
            )
            assert res.status_code == 403
        finally:
            self._stop(patches)

    def test_health_exempt_from_auth(self):
        patches = self._apply(self._clerk_patches(claims=None))
        try:
            res = client.get("/health")
            assert res.status_code == 200
        finally:
            self._stop(patches)


# ---------------------------------------------------------------------------
# auth_middleware — prototype password mode
# ---------------------------------------------------------------------------


class TestAuthMiddlewarePasswordMode:
    def _password_patches(self, token_in_set=False):
        token = "testtoken_abc123"
        valid_tokens = {token} if token_in_set else set()
        return token, [
            patch.object(main_module, "CLERK_ENABLED", False),
            patch.object(main_module, "PROTOTYPE_PASSWORD_ENABLED", True),
            patch.object(main_module, "VALID_AUTH_TOKENS", valid_tokens),
        ]

    def test_no_token_returns_401(self):
        _, patches = self._password_patches()
        for p in patches:
            p.start()
        try:
            res = client.get("/api/session")
            assert res.status_code == 401
        finally:
            for p in patches:
                p.stop()

    def test_invalid_token_returns_401(self):
        _, patches = self._password_patches()
        for p in patches:
            p.start()
        try:
            res = client.get("/api/session", headers={"X-Prototype-Auth": "wrongtoken"})
            assert res.status_code == 401
        finally:
            for p in patches:
                p.stop()

    def test_valid_token_passes(self):
        token, patches = self._password_patches(token_in_set=True)
        for p in patches:
            p.start()
        try:
            res = client.get("/api/session", headers={"X-Prototype-Auth": token})
            assert res.status_code not in (401, 403)
        finally:
            for p in patches:
                p.stop()

    def test_health_exempt(self):
        _, patches = self._password_patches()
        for p in patches:
            p.start()
        try:
            res = client.get("/health")
            assert res.status_code == 200
        finally:
            for p in patches:
                p.stop()

    def test_validate_endpoint_exempt_from_token_check(self):
        # /api/auth/validate must be reachable without an auth token (it IS the auth endpoint).
        # Use a correct password so the handler returns 200, proving middleware let it through.
        password = "correctpassword"  # pragma: allowlist secret
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        _, patches = self._password_patches()
        patches.append(patch.object(main_module, "PROTOTYPE_PASSWORD_HASH", pw_hash))
        for p in patches:
            p.start()
        try:
            res = client.post("/api/auth/validate", json={"password": password})
            assert res.status_code == 200
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# /api/auth/validate — password validation endpoint
# ---------------------------------------------------------------------------


class TestPasswordValidationEndpoint:
    def _patches(self, password="correctpassword"):  # pragma: allowlist secret
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        return [
            patch.object(main_module, "CLERK_ENABLED", False),
            patch.object(main_module, "PROTOTYPE_PASSWORD_ENABLED", True),
            patch.object(main_module, "PROTOTYPE_PASSWORD_HASH", pw_hash),
        ]

    def test_correct_password_returns_token(self):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            res = client.post(
                "/api/auth/validate",
                json={"password": "correctpassword"},  # pragma: allowlist secret
            )
            assert res.status_code == 200
            data = res.json()
            assert data["valid"] is True
            assert data["token"] is not None
        finally:
            for p in patches:
                p.stop()

    def test_wrong_password_returns_401(self):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            res = client.post(
                "/api/auth/validate",
                json={"password": "wrongpassword"},  # pragma: allowlist secret
            )
            assert res.status_code == 401
            assert res.json()["valid"] is False
        finally:
            for p in patches:
                p.stop()

    def test_issued_token_is_added_to_valid_set(self):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            res = client.post(
                "/api/auth/validate",
                json={"password": "correctpassword"},  # pragma: allowlist secret
            )
            token = res.json()["token"]
            assert token in main_module.VALID_AUTH_TOKENS
        finally:
            for p in patches:
                p.stop()

    def test_returns_404_when_clerk_enabled(self):
        # Must send a valid (mocked) JWT so middleware passes; handler then returns 404.
        patches = [
            patch.object(main_module, "CLERK_ENABLED", True),
            patch.object(main_module, "ALLOWED_EMAIL_DOMAINS", []),
            patch.object(
                main_module,
                "_decode_clerk_token",
                return_value={"email": "u@example.com"},
            ),
        ]
        for p in patches:
            p.start()
        try:
            res = client.post(
                "/api/auth/validate",
                json={"password": "anything"},  # pragma: allowlist secret
                headers={"Authorization": "Bearer faketoken"},
            )
            assert res.status_code == 404
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_security_headers_present_on_all_responses(self):
        res = client.get("/health")
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("x-frame-options") == "DENY"
        assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_hsts_absent_outside_production(self):
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            res = client.get("/health")
            assert "strict-transport-security" not in res.headers
