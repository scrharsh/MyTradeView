"""FYERS API v3 OAuth2 authentication: login URL, auth-code exchange, token cache, validation.

Security rules enforced here:
- Access tokens, auth codes and the app secret are never logged, returned by status
  methods, or included in raised error messages (``_scrub`` is applied everywhere).
- "Authenticated" means a real FYERS API request (profile) succeeded; token presence
  alone is never treated as authentication.
- The daily token is cached at ``FYERS_TOKEN_CACHE_PATH`` (default ./secrets/fyers_token.json)
  with owner-only permissions where the OS supports it.
"""

import asyncio
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from src.common.time_utils import IST_TZ, now_utc, to_ist

logger = logging.getLogger(__name__)

# FYERS access tokens are valid for one trading day and roll over early morning IST.
TOKEN_ROLLOVER_IST = time(6, 0, 0)
# FYERS error codes meaning the token/session is rejected.
AUTH_REJECT_CODES = {-8, -15, -16, -17, -300, -310, 401, 403}


class FyersAuthError(Exception):
    """Authentication failure with a sanitized (secret-free) message."""


def _default_session_factory(**kwargs: Any) -> Any:
    from fyers_apiv3 import fyersModel

    return fyersModel.SessionModel(**kwargs)


def _default_model_factory(client_id: str, token: str, log_path: str) -> Any:
    from fyers_apiv3 import fyersModel

    return fyersModel.FyersModel(client_id=client_id, token=token, is_async=False, log_path=log_path)


class FyersAuthManager:
    """Owns the FYERS OAuth flow, token storage and token validation."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        token_cache_path: Optional[Path] = None,
        session_factory: Optional[Callable[..., Any]] = None,
        model_factory: Optional[Callable[[str, str, str], Any]] = None,
        log_dir: Optional[Path] = None,
    ) -> None:
        env = os.environ
        self.client_id = client_id or env.get("FYERS_CLIENT_ID") or env.get("FYERS_APP_ID")
        self._secret_key = secret_key or env.get("FYERS_SECRET_KEY")
        self.redirect_uri = redirect_uri or env.get("FYERS_REDIRECT_URI") or "https://127.0.0.1:5000/login"
        self.token_cache_path = Path(
            token_cache_path or env.get("FYERS_TOKEN_CACHE_PATH") or "./secrets/fyers_token.json"
        )
        self._session_factory = session_factory or _default_session_factory
        self._model_factory = model_factory or _default_model_factory
        self._log_dir = Path(log_dir or "./logs")

        self._pending_state: Optional[str] = None
        self._validated: bool = False
        self._validated_at: Optional[datetime] = None
        self._reason: str = "NOT_VALIDATED"

    # ------------------------------------------------------------------
    # Sanitization
    # ------------------------------------------------------------------
    def _scrub(self, text: Any, extra: Optional[list] = None) -> str:
        out = str(text)
        secrets_to_hide = [self._secret_key, self._load_raw_token()] + list(extra or [])
        for s in secrets_to_hide:
            if s and len(s) >= 6:
                out = out.replace(s, "***")
        return out

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self._secret_key and self.redirect_uri)

    # ------------------------------------------------------------------
    # Token cache
    # ------------------------------------------------------------------
    def _rollover_boundary(self, now: Optional[datetime] = None) -> datetime:
        now_i = to_ist(now or now_utc())
        boundary = IST_TZ.localize(datetime.combine(now_i.date(), TOKEN_ROLLOVER_IST))
        if now_i < boundary:
            boundary -= timedelta(days=1)
        return boundary

    def _read_cache(self) -> Optional[Dict[str, Any]]:
        try:
            if self.token_cache_path.exists():
                with open(self.token_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else None
        except Exception:
            return None
        return None

    def _load_raw_token(self) -> Optional[str]:
        """Return the usable token (env first, then a same-session cache entry)."""
        env_token = os.environ.get("FYERS_ACCESS_TOKEN")
        if env_token:
            return env_token
        data = self._read_cache()
        if not data or not data.get("access_token"):
            return None
        issued = data.get("issued_at")
        try:
            issued_dt = datetime.fromisoformat(issued)
        except Exception:
            return None
        if issued_dt < self._rollover_boundary():
            return None  # previous trading day's token: expired by policy
        cached_app = data.get("app_id")
        if cached_app and self.client_id and cached_app != self.client_id:
            return None
        return data["access_token"]

    def _cached_token_is_expired(self) -> bool:
        data = self._read_cache()
        if not data or not data.get("access_token") or not data.get("issued_at"):
            return False
        try:
            return datetime.fromisoformat(data["issued_at"]) < self._rollover_boundary()
        except (TypeError, ValueError):
            return False

    def get_access_token(self) -> Optional[str]:
        """Token for in-process use only (e.g. WebSocket). Never log or return via API."""
        return self._load_raw_token()

    def build_model(self) -> Any:
        """Build an authenticated FYERS REST model for in-process broker use.

        The token is intentionally not returned by this method or included in
        diagnostics; callers receive only the SDK client instance.
        """
        token = self._load_raw_token()
        if not self.client_id or not token or not self._validated:
            raise FyersAuthError("FYERS session is not authenticated.")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        return self._model_factory(self.client_id, token, str(self._log_dir))

    def save_token(self, token: str) -> None:
        self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token,
            "app_id": self.client_id,
            "issued_at": now_utc().isoformat(),
        }
        fd, tmp = tempfile.mkstemp(dir=str(self.token_cache_path.parent), prefix=".tok_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.token_cache_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def clear_token(self) -> None:
        try:
            if self.token_cache_path.exists():
                self.token_cache_path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # OAuth flow
    # ------------------------------------------------------------------
    def get_login_url(self) -> str:
        """Build the FYERS authorization URL (contains client_id + state; no secrets)."""
        if not self.is_configured:
            raise FyersAuthError(
                "FYERS OAuth is not configured: FYERS_CLIENT_ID, FYERS_SECRET_KEY and FYERS_REDIRECT_URI are required."
            )
        self._pending_state = secrets.token_urlsafe(16)
        session = self._session_factory(
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            state=self._pending_state,
            secret_key=self._secret_key,
            grant_type="authorization_code",
        )
        return session.generate_authcode()

    def parse_auth_code(self, value: str) -> str:
        """Accept a bare auth_code or the full redirected URL; verify state when present."""
        value = (value or "").strip()
        if not value:
            raise FyersAuthError("No authorization code supplied.")
        if "auth_code=" in value or value.startswith("http"):
            qs = parse_qs(urlparse(value).query)
            code = (qs.get("auth_code") or [None])[0]
            if not code:
                raise FyersAuthError("Redirect URL does not contain an auth_code parameter.")
            state = (qs.get("state") or [None])[0]
            if state is not None and self._pending_state is not None and state != self._pending_state:
                raise FyersAuthError("OAuth state mismatch; restart login from the application.")
            return code
        return value

    def _exchange_sync(self, auth_code: str) -> str:
        session = self._session_factory(
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            grant_type="authorization_code",
            secret_key=self._secret_key,
        )
        session.set_token(auth_code)
        resp = session.generate_token()
        if not isinstance(resp, dict) or resp.get("s") != "ok" or not resp.get("access_token"):
            code = resp.get("code") if isinstance(resp, dict) else None
            msg = resp.get("message", "token exchange rejected") if isinstance(resp, dict) else "empty response"
            raise FyersAuthError(self._scrub(f"FYERS token exchange failed (code {code}): {msg}", [auth_code]))
        return resp["access_token"]

    async def exchange_auth_code(self, auth_code_or_url: str) -> Dict[str, Any]:
        """Exchange an authorization code for an access token, store it, then verify it live."""
        if not self.is_configured:
            raise FyersAuthError("FYERS OAuth is not configured.")
        code = self.parse_auth_code(auth_code_or_url)
        try:
            token = await asyncio.to_thread(self._exchange_sync, code)
        except FyersAuthError:
            raise
        except Exception as e:
            raise FyersAuthError(self._scrub(f"FYERS token exchange error: {type(e).__name__}", [code]))
        self.save_token(token)
        self._pending_state = None
        logger.info("[FyersAuth] Access token exchanged and stored (token redacted).")
        return await self.validate()

    # ------------------------------------------------------------------
    # Live validation
    # ------------------------------------------------------------------
    def _profile_sync(self, token: str) -> Dict[str, Any]:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        model = self._model_factory(self.client_id, token, str(self._log_dir))
        return model.get_profile()

    async def validate(self) -> Dict[str, Any]:
        """Authenticate for real: call the FYERS profile endpoint with the stored token."""
        token = self._load_raw_token()
        if not self.client_id or not token:
            self._set_state(False, "TOKEN_EXPIRED" if self._cached_token_is_expired() else "NO_TOKEN")
            return self.status()
        try:
            resp = await asyncio.wait_for(asyncio.to_thread(self._profile_sync, token), timeout=15)
        except asyncio.TimeoutError:
            self._set_state(False, "NETWORK_TIMEOUT")
            return self.status()
        except Exception as e:
            self._set_state(False, f"NETWORK_ERROR:{type(e).__name__}")
            return self.status()

        if isinstance(resp, dict) and resp.get("s") == "ok":
            self._set_state(True, "AUTHENTICATED")
        else:
            code = resp.get("code") if isinstance(resp, dict) else None
            msg = str(resp.get("message", "")) if isinstance(resp, dict) else ""
            if code in AUTH_REJECT_CODES or "token" in msg.lower() or "expired" in msg.lower():
                self.invalidate("TOKEN_EXPIRED_OR_INVALID")
            else:
                self._set_state(False, f"FYERS_ERROR_CODE_{code}")
        return self.status()

    def invalidate(self, reason: str) -> None:
        """Fail closed: mark session unauthenticated and discard a rejected cached token."""
        self._set_state(False, reason)
        if not os.environ.get("FYERS_ACCESS_TOKEN"):
            self.clear_token()

    def _set_state(self, ok: bool, reason: str) -> None:
        self._validated = ok
        self._validated_at = now_utc()
        self._reason = reason
        logger.info("[FyersAuth] validation=%s reason=%s", ok, reason)

    # ------------------------------------------------------------------
    @property
    def is_authenticated(self) -> bool:
        """True only if the last real API validation succeeded."""
        return self._validated

    def status(self) -> Dict[str, Any]:
        """Sanitized status: never contains the token, secret or auth code."""
        return {
            "oauth_configured": self.is_configured,
            "token_present": self._load_raw_token() is not None,
            "authenticated": self._validated,
            "validated_at": self._validated_at.isoformat() if self._validated_at else None,
            "reason": self._reason,
            "redirect_uri": self.redirect_uri,
        }
