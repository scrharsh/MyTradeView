"""Orders and positions inspection and square-off endpoints."""

from typing import List
from fastapi import APIRouter, HTTPException, Path
from src.api.models import OrderResponseItem, PositionResponseItem, SquareOffRequest
from src.api.service import workstation_service

router = APIRouter(prefix="", tags=["Orders & Positions"])


@router.get("/orders", response_model=List[OrderResponseItem])
async def list_orders() -> List[OrderResponseItem]:
    """Get list of all orders with status transitions and execution fills."""
    return workstation_service.list_orders()


@router.get("/positions", response_model=List[PositionResponseItem])
async def list_positions() -> List[PositionResponseItem]:
    """Get list of current open and closed positions."""
    return workstation_service.list_positions()


@router.post("/positions/{symbol:path}/square-off", response_model=PositionResponseItem)
async def square_off_position(
    symbol: str = Path(..., description="Position trading symbol to square off"),
    req: SquareOffRequest = SquareOffRequest(),
) -> PositionResponseItem:
    """Square off an individual position in PAPER mode."""
    try:
        return await workstation_service.square_off_position(
            symbol=symbol,
            reason=req.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

