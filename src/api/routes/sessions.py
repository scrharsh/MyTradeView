"""Trading session lifecycle endpoints."""

from typing import Any, Dict
from fastapi import APIRouter
from src.api.models import SessionActionRequest, SessionActiveResponse
from src.api.service import workstation_service

router = APIRouter(prefix="/sessions", tags=["Trading Sessions"])


@router.get("/active", response_model=SessionActiveResponse)
async def get_active_session() -> SessionActiveResponse:
    """Get active trading session, phase, opening range status, and strategy state."""
    return workstation_service.get_active_session()


@router.post("/start")
async def start_session() -> Dict[str, Any]:
    """Start or restart the paper trading session."""
    active = workstation_service.start_session()
    return {
        "status": "STARTED",
        "session_id": active.session_id,
        "execution_mode": "paper",
        "phase": active.phase,
    }


@router.post("/stop")
async def stop_session() -> Dict[str, Any]:
    """Gracefully stop the current trading session."""
    active = workstation_service.stop_session()
    return {
        "status": "STOPPED",
        "session_id": active.session_id,
        "execution_mode": "paper",
        "phase": active.phase,
    }


@router.post("/seed-demo")
async def seed_demo_fixtures() -> Dict[str, Any]:
    """Explicitly seed demo orders and positions for testing/demonstration."""
    return await workstation_service.seed_demo_orders()


@router.post("/clear-orders")
async def clear_orders_and_positions() -> Dict[str, Any]:
    """Clear all active and historical orders, positions, and MTM tracker state."""
    return await workstation_service.clear_orders_and_positions()

