"""Risk monitoring, telemetry, and kill-switch endpoints."""

from typing import List
from fastapi import APIRouter, Query
from src.api.models import (
    AuditEventResponseItem,
    KillSwitchRequest,
    KillSwitchResponse,
    RiskSnapshotResponse,
)
from src.api.service import workstation_service

router = APIRouter(prefix="/risk", tags=["Risk Management"])


@router.get("/snapshot", response_model=RiskSnapshotResponse)
async def get_risk_snapshot() -> RiskSnapshotResponse:
    """Get real-time risk snapshot, MTM telemetry, drawdown, and circuit breaker status."""
    return workstation_service.get_risk_snapshot()


@router.get("/audit-events", response_model=List[AuditEventResponseItem])
async def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200, description="Max audit events to return"),
) -> List[AuditEventResponseItem]:
    """Retrieve immutable audit events from repository persistence."""
    return await workstation_service.list_audit_events(limit=limit)


@router.post("/kill-switch", response_model=KillSwitchResponse)
async def trigger_kill_switch(req: KillSwitchRequest) -> KillSwitchResponse:
    """
    Trigger emergency kill-switch in PAPER mode.
    Cancels pending orders and closes all open positions at current simulated market prices.
    """
    return await workstation_service.trigger_kill_switch(
        reason=req.reason,
        operator=req.operator,
    )

