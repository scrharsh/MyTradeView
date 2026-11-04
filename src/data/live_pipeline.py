"""Live candle pipeline: FYERS ticks -> finalized 1-minute candles -> strategy callback.

Only finalized, validated candles are published. Any feed interruption (disconnect,
stale feed, auth failure) discards the in-progress candle so a partial/incorrect bar
can never reach the strategy (fail closed).
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from src.core.models import Candle
from src.data.candle_builder import TickCandleBuilder
from src.data.fyers_live_feed import FyersLiveFeed

logger = logging.getLogger(__name__)


class LiveCandlePipeline:
    def __init__(
        self,
        feed: FyersLiveFeed,
        builder: TickCandleBuilder,
        on_candle: Callable[[Candle], Awaitable[Any]],
        on_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.feed = feed
        self.builder = builder
        self._on_candle = on_candle
        self._on_event = on_event
        self._task: Optional[asyncio.Task] = None
        self.stats: Dict[str, int] = {"candles_published": 0, "publish_errors": 0}
        self.last_candle: Optional[Candle] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.feed.start()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        await self.feed.stop()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
            self._task = None
        self.builder.discard_partial()

    async def _run(self) -> None:
        async for ev in self.feed.events():
            if ev.kind == "tick" and ev.tick is not None:
                for candle in self.builder.on_tick(ev.tick):
                    await self._publish(candle)
            else:
                if self._on_event is not None:
                    try:
                        self._on_event(ev.kind, ev.reason)
                    except Exception:
                        pass
                # disconnect / stale / auth_failed: never publish a partial candle
                if self.builder.discard_partial():
                    logger.warning("[LiveFeed] Partial candle discarded (%s: %s).", ev.kind, ev.reason)

    async def _publish(self, candle: Candle) -> None:
        try:
            await self._on_candle(candle)
            self.last_candle = candle
            self.stats["candles_published"] += 1
        except Exception as e:  # keep the pipeline alive; the error is surfaced via stats
            self.stats["publish_errors"] += 1
            logger.error("[LiveFeed] Candle consumer failed: %s", type(e).__name__)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "feed": self.feed.snapshot(),
            "builder": dict(self.builder.stats),
            "pipeline": dict(self.stats),
            "last_candle_time": self.last_candle.timestamp.isoformat() if self.last_candle else None,
        }
