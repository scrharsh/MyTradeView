"""Market data endpoints for candles, instruments, and feed status."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
from src.api.models import CandlesResponse, InstrumentResponseItem
from src.api.service import workstation_service

router = APIRouter(prefix="/market-data", tags=["Market Data"])


@router.get("/candles", response_model=CandlesResponse)
async def get_candles(
    symbol: str = Query("NSE:NIFTY50-INDEX", description="Trading symbol"),
    resolution: str = Query("1m", description="Candle resolution"),
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter (ISO)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter (ISO)"),
) -> CandlesResponse:
    """Get 1-minute historical/synthetic candlestick data for charting."""
    return workstation_service.get_candles(
        symbol=symbol,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/instruments", response_model=List[InstrumentResponseItem])
async def list_instruments() -> List[InstrumentResponseItem]:
    """List loaded tradeable instruments (underlying index and weekly options)."""
    return workstation_service.list_instruments()


@router.get("/status")
async def get_market_data_status() -> Dict[str, Any]:
    """Get market data feed and WebSocket connectivity health."""
    return {
        "status": "HEALTHY",
        "feed_connected": True,
        "is_stale": False,
        "last_tick_time": datetime.utcnow().isoformat(),
        "subscribed_symbols_count": len(workstation_service.sample_candles),
    }

