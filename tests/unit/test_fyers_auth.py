"""Unit tests for FYERS OAuth handling using a mocked FYERS SDK (not live connectivity)."""

import json
import os
from datetime import timedelta

import pytest

from src.broker.fyers_auth import FyersAuthError, FyersAuthManager
from src.common.time_utils import now_utc

SECRET = "SUPER-SECRET-KEY-123"
TOKEN = "tok_abcdef0123456789_VALID"
CODE = "authcode_XYZ_987654"


class FakeSession:
    def __init__(self, **kw):
        self.kw = kw
        self.code = None

    def generate_authcode(self):
        return "https://api-t1.fyers.in/api/v3/generate-authcode?client_id=APP-100&state=" + self.kw.get("state", "")

    def set_token(self, c):
        self.code = c

    def generate_token(self):
        if self.code == CODE:
            return {"s": "ok", "access_token": TOKEN}
        return {"s": "error", "code": -413, "message": f"invalid auth code {self.code}"}


class FakeModel:
    def __init__(self, response):
        self.response = response

    def get_profile(self):
        return self.response


def make(tmp_path, profile=None, **kw):
    profile = profile if profile is not None else {"s": "ok", "code": 200, "data": {"name": "X"}}
    return FyersAuthManager(
        client_id="APP-100",
        secret_key=SECRET,
        redirect_uri="https://127.0.0.1:5000/login",
        token_cache_path=tmp_path / "secrets" / "fyers_token.json",
        session_factory=lambda **k: FakeSession(**k),
        model_factory=lambda cid, tok, lp: FakeModel(profile),
        log_dir=tmp_path / "logs",
        **kw,
    )


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    for k in ("FYERS_ACCESS_TOKEN", "FYERS_CLIENT_ID", "FYERS_APP_ID", "FYERS_SECRET_KEY", "FYERS_REDIRECT_URI", "FYERS_TOKEN_CACHE_PATH"):
        monkeypatch.delenv(k, raising=False)


def test_login_url_generated_and_state_stored(tmp_path):
    m = make(tmp_path)
    url = m.get_login_url()
    assert "generate-authcode" in url and m._pending_state in url
    assert SECRET not in url


def test_login_requires_oauth_config(tmp_path):
    m = FyersAuthManager(client_id=None, secret_key=None, token_cache_path=tmp_path / "t.json")
    with pytest.raises(FyersAuthError):
        m.get_login_url()


def test_parse_auth_code_from_redirect_url_and_state_check(tmp_path):
    m = make(tmp_path)
    m.get_login_url()
    st = m._pending_state
    assert m.parse_auth_code(f"https://127.0.0.1:5000/login?s=ok&code=200&auth_code={CODE}&state={st}") == CODE
    with pytest.raises(FyersAuthError):
        m.parse_auth_code(f"https://127.0.0.1:5000/login?auth_code={CODE}&state=WRONG")
    assert m.parse_auth_code(CODE) == CODE


async def test_exchange_stores_token_then_validates_with_real_request(tmp_path):
    m = make(tmp_path)
    st = await m.exchange_auth_code(CODE)
    assert st["authenticated"] is True and st["reason"] == "AUTHENTICATED"
    saved = json.loads(m.token_cache_path.read_text())
    assert saved["access_token"] == TOKEN and saved["app_id"] == "APP-100"
    # status never leaks secrets
    dump = json.dumps(m.status())
    assert TOKEN not in dump and SECRET not in dump and CODE not in dump


async def test_exchange_failure_is_sanitized(tmp_path):
    m = make(tmp_path)
    with pytest.raises(FyersAuthError) as ei:
        await m.exchange_auth_code("badcode_123456")
    assert "badcode_123456" not in str(ei.value)
    assert not m.token_cache_path.exists()
    assert m.is_authenticated is False


async def test_token_presence_alone_is_not_authentication(tmp_path):
    m = make(tmp_path)
    m.save_token(TOKEN)
    assert m.status()["token_present"] is True
    assert m.is_authenticated is False  # nothing validated yet


async def test_expired_cached_token_reports_expiry(tmp_path):
    m = make(tmp_path)
    m.save_token(TOKEN)
    data = json.loads(m.token_cache_path.read_text())
    data["issued_at"] = (now_utc() - timedelta(days=2)).isoformat()
    m.token_cache_path.write_text(json.dumps(data))

    st = await m.validate()
    assert st["authenticated"] is False
    assert st["reason"] == "TOKEN_EXPIRED"


async def test_rejected_token_fails_closed_and_is_discarded(tmp_path):
    m = make(tmp_path, profile={"s": "error", "code": -16, "message": "Could not authenticate the user"})
    m.save_token(TOKEN)
    st = await m.validate()
    assert st["authenticated"] is False and st["reason"] == "TOKEN_EXPIRED_OR_INVALID"
    assert not m.token_cache_path.exists()


async def test_network_error_keeps_token_but_not_authenticated(tmp_path):
    m = make(tmp_path)
    m.save_token(TOKEN)

    def boom(cid, tok, lp):
        raise ConnectionError("down")

    m._model_factory = boom
    st = await m.validate()
    assert st["authenticated"] is False and st["reason"].startswith("NETWORK_ERROR")
    assert m.token_cache_path.exists()


def test_previous_day_token_is_expired(tmp_path):
    m = make(tmp_path)
    m.save_token(TOKEN)
    data = json.loads(m.token_cache_path.read_text())
    data["issued_at"] = (now_utc() - timedelta(days=2)).isoformat()
    m.token_cache_path.write_text(json.dumps(data))
    assert m.get_access_token() is None
    assert m.status()["token_present"] is False


def test_env_token_is_supported_but_still_needs_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", TOKEN)
    m = make(tmp_path)
    assert m.get_access_token() == TOKEN
    assert m.is_authenticated is False


def test_token_file_not_world_readable_on_posix(tmp_path):
    m = make(tmp_path)
    m.save_token(TOKEN)
    if os.name == "posix":
        assert (m.token_cache_path.stat().st_mode & 0o077) == 0
