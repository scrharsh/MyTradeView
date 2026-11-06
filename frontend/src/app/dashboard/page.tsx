"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronRight, ExternalLink, RefreshCw } from "lucide-react";
import { TradingChart } from "../../components/TradingChart";
import { api } from "../../lib/api";
import { formatINR, formatISTDateTime, formatISTTime, getPnlColor } from "../../lib/formatters";
import {
  CandlesResponse,
  LiveFeedResponse,
  MarketDataStatusResponse,
  OrderResponseItem,
  PositionResponseItem,
  RiskSnapshotResponse,
  SessionActiveResponse,
  SystemHealthResponse,
} from "../../types/api";

const na = "N/A";

function value(value: unknown): string {
  return value === null || value === undefined || value === "" ? na : String(value);
}

function numberValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function decimal(value: unknown, digits = 2): string {
  const parsed = numberValue(value);
  return parsed === null ? na : parsed.toFixed(digits);
}

function StatusDot({ ok, warn = false }: { ok: boolean; warn?: boolean }) {
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400" : warn ? "bg-amber-400" : "bg-rose-400"}`} />;
}

function Metric({ label, value: metricValue, tone = "text-slate-100", detail }: { label: string; value: React.ReactNode; tone?: string; detail?: React.ReactNode }) {
  return (
    <div className="min-w-0 border-l border-[#1c2738] pl-3 first:border-l-0 first:pl-0">
      <div className="text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</div>
      <div className={`mt-1 truncate font-mono text-sm font-semibold tabular-nums ${tone}`}>{metricValue}</div>
      {detail ? <div className="mt-0.5 truncate text-[10px] text-slate-500">{detail}</div> : null}
    </div>
  );
}

function Section({ title, eyebrow, action, children, className = "" }: { title: string; eyebrow?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel overflow-hidden ${className}`}>
      <div className="flex min-h-11 items-center justify-between border-b border-[#1a2336] bg-[#0a0e16] px-3.5">
        <div>
          {eyebrow ? <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-cyan-500/80">{eyebrow}</div> : null}
          <h2 className="font-mono text-xs font-semibold uppercase tracking-[0.12em] text-slate-200">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export default function DashboardPage() {
  const [risk, setRisk] = useState<RiskSnapshotResponse | null>(null);
  const [session, setSession] = useState<SessionActiveResponse | null>(null);
  const [health, setHealth] = useState<SystemHealthResponse | null>(null);
  const [market, setMarket] = useState<MarketDataStatusResponse | null>(null);
  const [live, setLive] = useState<LiveFeedResponse | null>(null);
  const [candles, setCandles] = useState<CandlesResponse | null>(null);
  const [positions, setPositions] = useState<PositionResponseItem[]>([]);
  const [orders, setOrders] = useState<OrderResponseItem[]>([]);
  const [reconciliation, setReconciliation] = useState<{ is_synchronized?: boolean; summary?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [squaringOff, setSquaringOff] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const responses = await Promise.allSettled([
      api.getRiskSnapshot(),
      api.getActiveSession(),
      api.getHealth(),
      api.getMarketDataStatus(),
      api.getLiveFeedStatus(),
      api.getCandles("NSE:NIFTY50-INDEX", "1m"),
      api.getPositions(),
      api.getOrders(),
      api.getLatestReconciliation(),
    ]);
    const [riskRes, sessionRes, healthRes, marketRes, liveRes, candlesRes, positionsRes, ordersRes, reconciliationRes] = responses;
    if (riskRes.status === "fulfilled") setRisk(riskRes.value);
    if (sessionRes.status === "fulfilled") setSession(sessionRes.value);
    if (healthRes.status === "fulfilled") setHealth(healthRes.value);
    if (marketRes.status === "fulfilled") setMarket(marketRes.value);
    if (liveRes.status === "fulfilled") setLive(liveRes.value);
    if (candlesRes.status === "fulfilled") setCandles(candlesRes.value);
    if (positionsRes.status === "fulfilled") setPositions(Array.isArray(positionsRes.value) ? positionsRes.value : []);
    if (ordersRes.status === "fulfilled") setOrders(Array.isArray(ordersRes.value) ? ordersRes.value : []);
    if (reconciliationRes.status === "fulfilled") setReconciliation(reconciliationRes.value);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const squareOff = async (symbol: string) => {
    if (!window.confirm(`Confirm square-off for ${symbol}?`)) return;
    setSquaringOff(symbol);
    try {
      await api.squareOffPosition(symbol);
      setMessage(`Square-off submitted for ${symbol}.`);
      await refresh();
    } catch (error) {
      setMessage(`Square-off failed: ${error instanceof Error ? error.message : "request rejected"}`);
    } finally {
      setSquaringOff(null);
      window.setTimeout(() => setMessage(null), 5000);
    }
  };

  const source = health?.data_source ?? market?.data_source ?? session?.data_source ?? na;
  const mode = (health?.execution_mode ?? session?.execution_mode ?? na).toUpperCase();
  const marketOpen = Boolean(health?.is_market_open ?? market?.is_market_open);
  const feedState = health?.websocket_state ?? market?.feed_status ?? live?.state ?? "NOT_STARTED";
  const feedFresh = Boolean(health?.live_feed_fresh_tick ?? market?.fresh_tick_received ?? live?.fresh_tick_received);
  const feedConnected = Boolean(health?.market_data_feed_connected ?? market?.feed_connected);
  const authOk = Boolean(health?.fyers_authenticated ?? live?.authentication?.authenticated);
  const orb = session?.opening_range;
  const orbComplete = Boolean(orb?.is_established && orb.candle_count === 30);
  const actualTimestamp = market?.current_time_ist ?? (health?.timestamp ? formatISTDateTime(health.timestamp) : na);
  const dataStale = Boolean(health?.market_data_stale || market?.is_stale || (!feedFresh && source === "LIVE"));
  const mtm = numberValue(risk?.total_mtm);
  const remainingBuffer = risk && mtm !== null ? Math.max(0, Number(risk.max_daily_loss_limit) + mtm) : null;
  const bufferPct = risk && remainingBuffer !== null ? Math.max(0, Math.min(100, (remainingBuffer / Number(risk.max_daily_loss_limit)) * 100)) : null;
  const liveStats = live?.feed?.feed?.stats ?? {};
  const builderStats = live?.feed?.builder ?? {};
  const chartCandles = candles?.candles ?? [];
  const chartSource = candles?.data_source ?? candles?.provenance ?? na;
  const orderSummary = useMemo(() => orders.slice(0, 7), [orders]);

  return (
    <div className="space-y-3 pb-6 font-mono">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1a2336] pb-2">
        <div><div className="text-[10px] uppercase tracking-[0.22em] text-cyan-500">Execution supervision / NIFTY ORB</div><h1 className="mt-1 text-lg font-semibold tracking-tight text-slate-100">Live Terminal</h1></div>
        <button onClick={refresh} disabled={refreshing} className="btn-secondary px-2.5 py-1.5 text-[10px]" title="Refresh terminal telemetry"><RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} /> SYNC</button>
      </div>
      {message ? <div className="border border-cyan-800/60 bg-cyan-950/30 px-3 py-2 text-[11px] text-cyan-200">{message}</div> : null}

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded border border-[#1a2336] bg-[#1a2336] md:grid-cols-4 xl:grid-cols-8">
        {[["MODE", mode, mode === "LIVE" ? "text-rose-300" : "text-amber-300"],["SOURCE", source, source === "LIVE" ? "text-emerald-300" : "text-amber-300"],["FYERS", authOk ? "AUTHENTICATED" : (health?.fyers_status ?? "UNAUTHENTICATED"), authOk ? "text-emerald-300" : "text-amber-300"],["MARKET", marketOpen ? "OPEN" : "CLOSED", marketOpen ? "text-emerald-300" : "text-slate-400"],["FEED", feedState.replaceAll("_", " "), feedConnected && feedFresh ? "text-emerald-300" : "text-amber-300"],["PHASE", value(session?.phase), session?.phase === "ACTIVE_TRADING" ? "text-emerald-300" : "text-slate-300"],["STRATEGY", value(session?.strategy_state), "text-cyan-300"],["IST", actualTimestamp, "text-slate-200"]].map(([label, text, tone]) => <div key={label} className="bg-[#0d111a] px-2.5 py-2"><div className="text-[8px] uppercase tracking-[0.15em] text-slate-500">{label}</div><div className={`mt-1 truncate text-[11px] font-semibold ${tone}`}>{text}</div></div>)}
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.65fr)_minmax(330px,0.75fr)]">
        <Section title="NIFTY 50 / 1 minute" eyebrow={`Market data / ${chartSource}`} action={<span className={`flex items-center gap-1 text-[10px] ${dataStale ? "text-rose-300" : "text-slate-400"}`}><StatusDot ok={!dataStale && feedConnected} warn={!feedConnected && !dataStale} /> {dataStale ? "STALE / UNAVAILABLE" : source}</span>}>
          <div className="relative p-2">{chartCandles.length ? <TradingChart spotCandles={chartCandles} optionCandles={[]} orbHigh={numberValue(orb?.high) ?? undefined} orbLow={numberValue(orb?.low) ?? undefined} /> : <div className="flex h-[360px] items-center justify-center border border-dashed border-[#25334a] text-xs text-slate-500">{loading ? "Loading market data..." : `No chart candles available (${chartSource}).`}</div>}</div>
        </Section>
        <div className="space-y-3">
          <Section title="Opening range" eyebrow="Approved boundary / completed candles"><div className="grid grid-cols-2 gap-3 p-3"><Metric label="Window" value="09:16–09:45 IST" detail="First evaluation 09:46" /><Metric label="Integrity" value={`${orb?.candle_count ?? 0} / 30`} tone={orbComplete ? "text-emerald-300" : "text-amber-300"} detail={orbComplete ? "COMPLETE" : "INCOMPLETE / WAITING"} /><Metric label="OR high" value={decimal(orb?.high)} /><Metric label="OR low" value={decimal(orb?.low)} /><Metric label="Width" value={decimal(orb?.range_width, 1)} detail="index points" /><Metric label="Status" value={orb?.is_established ? "ESTABLISHED" : "NOT ESTABLISHED"} tone={orb?.is_established ? "text-emerald-300" : "text-slate-300"} /></div></Section>
          <Section title="Strategy monitor" eyebrow="Signal and exposure state"><div className="space-y-2 p-3 text-[11px]"><div className="grid grid-cols-2 gap-2"><Metric label="State" value={value(session?.strategy_state)} tone="text-cyan-300" /><Metric label="Position" value={positions.length ? `${positions.length} open` : "FLAT"} /><Metric label="Contract" value={value(session?.active_contract_symbol)} /><Metric label="Entry eligibility" value={orbComplete && session?.phase === "ACTIVE_TRADING" ? "ELIGIBLE WINDOW" : "BLOCKED"} tone={orbComplete && session?.phase === "ACTIVE_TRADING" ? "text-emerald-300" : "text-amber-300"} /></div><div className="flex items-center justify-between border-t border-[#1a2336] pt-2 text-slate-400"><span>Scheduled square-off</span><strong className="text-amber-300">15:10 IST</strong></div><div className="flex items-center justify-between text-slate-400"><span>Trading date</span><strong className="text-slate-200">{value(session?.trading_date)}</strong></div></div></Section>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.25fr_0.95fr_0.8fr]">
        <Section title="Positions" eyebrow={`${positions.length} open`} action={<Link href="/orders" className="text-[10px] text-cyan-400">ORDER BLOTTER <ChevronRight className="inline h-3 w-3" /></Link>}><div className="overflow-x-auto"><table className="w-full min-w-[620px] text-left text-[10px]"><thead className="border-b border-[#1a2336] text-[9px] uppercase tracking-wider text-slate-500"><tr><th className="px-3 py-2">Instrument</th><th>Qty</th><th>Avg</th><th>LTP</th><th>P&amp;L</th><th /></tr></thead><tbody className="divide-y divide-[#141d2b]">{positions.length ? positions.map((position) => { const pnl = numberValue(position.total_pnl); return <tr key={position.position_id} className="text-slate-300"><td className="px-3 py-2.5 font-semibold text-slate-100">{position.symbol}</td><td>{position.net_qty}</td><td>{decimal(position.avg_buy_price)}</td><td>{decimal(position.current_ltp)}</td><td className={pnl === null ? "text-slate-500" : getPnlColor(pnl)}>{pnl === null ? na : formatINR(pnl)}</td><td className="pr-3 text-right"><button onClick={() => squareOff(position.symbol)} disabled={squaringOff === position.symbol} className="btn-danger px-2 py-1 text-[9px]">{squaringOff === position.symbol ? "WAIT" : "SQUARE OFF"}</button></td></tr>; }) : <tr><td colSpan={6} className="px-3 py-7 text-center text-slate-500">No open derivative positions.</td></tr>}</tbody></table></div></Section>
        <Section title="Risk monitor" eyebrow="Paper risk engine"><div className="grid grid-cols-2 gap-3 p-3"><Metric label="Net MTM" value={mtm === null ? na : formatINR(mtm)} tone={mtm === null ? "text-slate-500" : getPnlColor(mtm)} /><Metric label="Drawdown" value={risk ? formatINR(risk.current_drawdown) : na} tone="text-rose-300" /><Metric label="Realized" value={risk ? formatINR(risk.realized_pnl) : na} /><Metric label="Unrealized" value={risk ? formatINR(risk.unrealized_pnl) : na} /><Metric label="Loss limit" value={risk ? formatINR(risk.max_daily_loss_limit) : na} detail={remainingBuffer === null ? "buffer N/A" : `${formatINR(remainingBuffer)} remaining`} /><Metric label="Profit lock" value={risk?.is_profit_protection_active ? "ACTIVE" : "INACTIVE"} tone={risk?.is_profit_protection_active ? "text-emerald-300" : "text-slate-300"} detail="trigger ₹3,000 / floor ₹500" /></div><div className="mx-3 mb-3 h-1.5 overflow-hidden bg-[#182336]"><div className={`h-full ${bufferPct !== null && bufferPct < 25 ? "bg-rose-400" : "bg-emerald-400"}`} style={{ width: `${bufferPct ?? 0}%` }} /></div><div className="flex items-center justify-between border-t border-[#1a2336] px-3 py-2 text-[10px]"><span className="text-slate-500">KILL SWITCH</span><strong className={risk?.is_kill_switch_active ? "text-rose-300" : "text-emerald-300"}>{risk?.is_kill_switch_active ? "ACTIVE" : "ARMED"}</strong></div></Section>
        <Section title="System health" eyebrow="Independent subsystems"><div className="space-y-2 p-3 text-[10px]">{[["FYERS REST", authOk, health?.fyers_status],["WebSocket", Boolean(health?.websocket_state && health.websocket_state !== "NOT_STARTED" && health.websocket_state !== "FAILED"), feedState.replaceAll("_", " ")],["Fresh tick", feedFresh, market?.last_tick_time ?? live?.feed?.feed?.last_tick_received_at],["Candle builder", source === "LIVE" ? Boolean(live?.feed?.pipeline) : source, market?.last_candle_time ?? live?.feed?.last_candle_time],["Database", Boolean(health?.database_connected), health?.database_connected ? "CONNECTED" : "DISCONNECTED"],["Reconciliation", Boolean(reconciliation?.is_synchronized ?? health?.is_reconciled), reconciliation?.summary ?? (health?.is_reconciled ? "SYNC" : "DIVERGENT")]].map(([label, ok, detail]) => <div key={label as string} className="flex items-center justify-between border-b border-[#141d2b] pb-1.5"><span className="flex items-center gap-2 text-slate-400"><StatusDot ok={Boolean(ok)} warn={label === "Candle builder" && source !== "LIVE"} />{label}</span><span className={Boolean(ok) ? "text-emerald-300" : label === "Candle builder" && source !== "LIVE" ? "text-amber-300" : "text-rose-300"}>{value(detail)}</span></div>)}<div className="pt-1 text-[9px] text-slate-600">Reconnects: {value(market?.reconnect_count)}</div></div></Section>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_0.85fr]"><Section title="Execution blotter" eyebrow={`${orders.length} session orders`} action={<Link href="/orders" className="text-[10px] text-cyan-400">FULL HISTORY <ExternalLink className="inline h-3 w-3" /></Link>}><div className="divide-y divide-[#141d2b]">{orderSummary.length ? orderSummary.map((order) => <div key={order.client_order_id} className="grid grid-cols-[1.4fr_0.45fr_0.65fr_0.75fr_1fr] items-center gap-2 px-3 py-2 text-[10px]"><span className="truncate text-slate-200">{order.symbol}</span><span className={order.side === "BUY" ? "text-emerald-300" : "text-rose-300"}>{order.side}</span><span className="text-slate-400">{order.filled_qty}/{order.qty}</span><span className="text-slate-300">{decimal(order.avg_fill_price)}</span><span className="text-right text-slate-500">{value(order.oms_status)} · {formatISTTime(order.updated_at)}</span></div>) : <div className="px-3 py-8 text-center text-slate-500">No orders in the current session.</div>}</div></Section><Section title="Data integrity" eyebrow="Provenance and freshness"><div className="grid grid-cols-2 gap-3 p-3"><Metric label="Chart provenance" value={chartSource} tone={candles?.is_synthetic ? "text-amber-300" : "text-emerald-300"} /><Metric label="Candle count" value={candles ? candles.candle_count : na} /><Metric label="Raw ticks" value={live ? value(liveStats.raw_ticks_received ?? liveStats.ticks_received) : na} /><Metric label="Builder accepted" value={live ? value(builderStats.ticks_accepted) : na} /><Metric label="Outside session" value={live ? value(builderStats.outside_session_ticks) : na} /><Metric label="Synthetic" value={candles ? (candles.is_synthetic ? "YES" : "NO") : na} tone={candles?.is_synthetic ? "text-amber-300" : "text-slate-300"} /></div></Section></div>
    </div>
  );
}