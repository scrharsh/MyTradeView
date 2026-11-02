"""Backtest execution, history, and StockMock benchmark comparison endpoints."""

from typing import List
from fastapi import APIRouter, HTTPException
from src.api.models import (
    BacktestDetailsResponse,
    BacktestRunRequest,
    BacktestSummaryResponse,
    BenchmarkRequest,
    BenchmarkResponse,
)
from src.api.service import workstation_service

router = APIRouter(prefix="/backtest", tags=["Backtesting Lab"])


@router.post("/run", response_model=BacktestDetailsResponse)
async def run_backtest(req: BacktestRunRequest) -> BacktestDetailsResponse:
    """Launch discrete-event backtest simulation and return full performance analytics."""
    try:
        return await workstation_service.run_backtest(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/runs", response_model=List[BacktestSummaryResponse])
async def list_backtest_runs() -> List[BacktestSummaryResponse]:
    """List historical backtest run summaries."""
    return await workstation_service.list_backtest_runs()


@router.get("/{run_id}", response_model=BacktestDetailsResponse)
async def get_backtest_run(run_id: str) -> BacktestDetailsResponse:
    """Get complete backtest report, diagnostics, equity curve, and trade journal."""
    try:
        return await workstation_service.get_backtest_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/benchmark", response_model=BenchmarkResponse)
async def benchmark_with_stockmock(req: BenchmarkRequest) -> BenchmarkResponse:
    """Upload and compare StockMock CSV benchmark with a backtest run."""
    try:
        return await workstation_service.benchmark_with_stockmock(
            run_id=req.run_id,
            csv_content=req.stockmock_csv_content,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

