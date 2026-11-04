"""Market data endpoints for candles, instruments, and feed status."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from src.api.models import CandlesResponse, InstrumentResponseItem
from src.api.service import workstation_service
from src.broker.fyers_auth import FyersAuthError

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
    """Get dynamic market data feed and WebSocket connectivity health."""
    return workstation_service.get_market_data_status()


@router.get("/live/status")
async def get_live_feed_status() -> Dict[str, Any]:
    """Distinct authentication / WebSocket / subscription / fresh-tick states of the live feed."""
    return workstation_service.get_live_feed_status()


@router.post("/live/start")
async def start_live_feed() -> Dict[str, Any]:
    """Start the real FYERS WebSocket feed (requires a validated FYERS session; PAPER execution only)."""
    try:
        return await workstation_service.start_live_feed()
    except FyersAuthError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Live feed could not start: {type(e).__name__}")


@router.post("/live/stop")
async def stop_live_feed() -> Dict[str, Any]:
    """Stop the live feed and discard any partial candle."""
    return await workstation_service.stop_live_feed()
