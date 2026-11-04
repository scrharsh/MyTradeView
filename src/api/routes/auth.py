"""FYERS OAuth2 authentication endpoints. Tokens, secrets and auth codes are never returned."""

from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.service import workstation_service
from src.broker.fyers_auth import FyersAuthError

router = APIRouter(prefix="/auth/fyers", tags=["FYERS Authentication"])


class FyersExchangeRequest(BaseModel):
    """Either the bare auth_code or the full redirected URL (containing auth_code=...)."""

    auth_code_or_url: str = Field(min_length=1, repr=False)


@router.get("/login-url")
async def get_login_url() -> Dict[str, Any]:
    """Return the FYERS authorization URL to open in a browser (login + 2FA happen at FYERS)."""
    try:
        return await workstation_service.fyers_login_url()
    except FyersAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/exchange")
async def exchange_auth_code(req: FyersExchangeRequest) -> Dict[str, Any]:
    """Exchange the authorization code for the daily access token, store it, and verify it live."""
    try:
        return await workstation_service.fyers_exchange_code(req.auth_code_or_url)
    except FyersAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def get_auth_status(validate: bool = False) -> Dict[str, Any]:
    """Sanitized auth status. With validate=true, performs a real FYERS profile request."""
    if validate:
        return await workstation_service.fyers_validate()
    return workstation_service.fyers_auth.status()


@router.post("/logout")
async def logout() -> Dict[str, Any]:
    """Stop the live feed and delete the cached access token."""
    return await workstation_service.fyers_logout()

