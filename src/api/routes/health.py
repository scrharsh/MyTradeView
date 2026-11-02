"""Health and system telemetry endpoints."""

from fastapi import APIRouter
from src.api.models import SystemHealthResponse
from src.api.service import workstation_service

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=SystemHealthResponse)
async def get_health() -> SystemHealthResponse:
    """Get system health, execution mode, broker connection, and DB status."""
    return await workstation_service.get_system_health()

