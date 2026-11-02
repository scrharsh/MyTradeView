"""Risk monitoring, telemetry, and kill-switch endpoints."""

from fastapi import APIRouter
from src.api.models import KillSwitchRequest, KillSwitchResponse, RiskSnapshotResponse
from src.api.service import workstation_service

router = APIRouter(prefix="/risk", tags=["Risk Management"])


@router.get("/snapshot", response_model=RiskSnapshotResponse)
async def get_risk_snapshot() -> RiskSnapshotResponse:
    """Get real-time risk snapshot, MTM telemetry, drawdown, and circuit breaker status."""
    return workstation_service.get_risk_snapshot()


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

