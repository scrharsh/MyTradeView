"""FastAPI Application entry point for MyTradeView Trading Workstation.

Exposes:
- REST API under /api/v1 (health, strategies, market data, sessions, risk, orders, backtest, reconciliation)
- WebSocket telemetry stream under /ws/telemetry
- Lifespan initialization of database, paper broker, and state repository
"""

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes.auth import router as auth_router
from src.api.routes.backtest import router as backtest_router
from src.api.routes.health import router as health_router
from src.api.routes.market_data import router as market_data_router
from src.api.routes.orders import router as orders_router
from src.api.routes.reconciliation import router as reconciliation_router
from src.api.routes.risk import router as risk_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.strategies import router as strategies_router
from src.api.service import workstation_service
from src.api.websocket import ws_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for application startup and graceful shutdown."""
    logger.info("Starting MyTradeView Trading Workstation Backend API (PAPER mode)...")
    await workstation_service.initialize()
    yield
    logger.info("Shutting down MyTradeView Backend API...")
    await workstation_service.stop_live_feed()
    if workstation_service.paper_broker and hasattr(workstation_service.paper_broker, "disconnect"):
        await workstation_service.paper_broker.disconnect()


app = FastAPI(
    title="MyTradeView Trading Workstation API",
    description="Deterministic options trading engine API with paper simulation, backtesting lab, and risk monitoring.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register REST Routers under /api/v1
API_V1_PREFIX = "/api/v1"
app.include_router(health_router, prefix=API_V1_PREFIX)
app.include_router(auth_router, prefix=API_V1_PREFIX)
app.include_router(strategies_router, prefix=API_V1_PREFIX)
app.include_router(market_data_router, prefix=API_V1_PREFIX)
app.include_router(sessions_router, prefix=API_V1_PREFIX)
app.include_router(risk_router, prefix=API_V1_PREFIX)
app.include_router(orders_router, prefix=API_V1_PREFIX)
app.include_router(backtest_router, prefix=API_V1_PREFIX)
app.include_router(reconciliation_router, prefix=API_V1_PREFIX)


# WebSocket Telemetry Endpoint
@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """WebSocket stream for real-time market data ticks, orders, and risk telemetry."""
    await ws_manager.connect(websocket)
    try:
        # Send initial snapshot upon connection
        risk = workstation_service.get_risk_snapshot()
        session = workstation_service.get_active_session()
        await websocket.send_json(
            {
                "event": "CONNECTED",
                "session": session.model_dump(mode="json"),
                "risk": risk.model_dump(mode="json"),
            }
        )

        while True:
            # Keep connection alive, listen for ping or client subscriptions
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("WebSocket connection encountered error: %s", exc)
        await ws_manager.disconnect(websocket)
