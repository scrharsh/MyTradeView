"""Broker and OMS state reconciliation endpoints."""

from typing import Any, Dict
from fastapi import APIRouter
from src.api.service import workstation_service

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.post("/run")
async def run_reconciliation() -> Dict[str, Any]:
    """Execute real-time OMS vs Broker reconciliation check."""
    report = await workstation_service.run_reconciliation()
    return report.model_dump(mode="json")


@router.get("/latest")
async def get_latest_reconciliation() -> Dict[str, Any]:
    """Get the latest reconciliation audit report."""
    report = workstation_service.get_latest_reconciliation()
    if not report:
        # Run one immediately if none exists
        report = await workstation_service.run_reconciliation()
    return report.model_dump(mode="json")
