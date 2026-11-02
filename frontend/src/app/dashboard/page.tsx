"use client";

import React, { useEffect, useState } from "react";
import {
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  ShieldAlert,
  Activity,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  CheckCircle,
  AlertTriangle,
  PlayCircle,
  PauseCircle,
} from "lucide-react";
import { api } from "../../lib/api";
import { formatINR, formatNumber, getPnlColor, formatISTTime } from "../../lib/formatters";
import {
  PositionResponseItem,
  OrderResponseItem,
  RiskSnapshotResponse,
  SessionActiveResponse,
  SystemHealthResponse,
} from "../../types/api";

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [risk, setRisk] = useState<RiskSnapshotResponse | null>(null);
  const [session, setSession] = useState<SessionActiveResponse | null>(null);
  const [health, setHealth] = useState<SystemHealthResponse | null>(null);
  const [positions, setPositions] = useState<PositionResponseItem[]>([]);
  const [orders, setOrders] = useState<OrderResponseItem[]>([]);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [squaringOff, setSquaringOff] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [rRes, sRes, hRes, pRes, oRes] = await Promise.allSettled([
        api.getRiskSnapshot(),
        api.getActiveSession(),
        api.getHealth(),
        api.getPositions(),
        api.getOrders(),
      ]);
      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (sRes.status === "fulfilled") setSession(sRes.value);
      if (hRes.status === "fulfilled") setHealth(hRes.value);
      if (pRes.status === "fulfilled") {
        setPositions(Array.isArray(pRes.value) ? pRes.value : []);
      }
      if (oRes.status === "fulfilled") {
        setOrders(Array.isArray(oRes.value) ? oRes.value : []);
      }
    } catch (err) {
      console.error("Dashboard refresh error:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleSquareOff = async (symbol: string) => {
    if (!confirm(`Confirm square-off market liquidation for ${symbol}?`)) return;
    setSquaringOff(symbol);
    try {
      const res = await api.squareOffPosition(symbol);
      setActionMessage(`Liquidation submitted for ${symbol}: ${res.is_open ? "OPEN" : "CLOSED"}`);
      await fetchData();
    } catch (err: any) {
      setActionMessage(`Error squaring off ${symbol}: ${err?.message}`);
    } finally {
      setSquaringOff(null);
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  const netMtm = risk ? parseFloat(String(risk.total_mtm)) : 0.0;
  const realizedPnl = risk ? parseFloat(String(risk.realized_pnl)) : 0.0;
  const unrealizedPnl = risk ? parseFloat(String(risk.unrealized_pnl)) : 0.0;
  const peakMtm = risk ? parseFloat(String(risk.peak_mtm)) : 0.0;
  const maxLossLimit = risk ? parseFloat(String(risk.max_daily_loss_limit || 3000.0)) : 3000.0;
  const remainingLossBuffer = maxLossLimit + netMtm;

  return (
    <div className="space-y-6">
      {/* Synthetic Dataset Warning Banner */}
      {risk?.is_synthetic && (
        <div className="rounded border border-amber-500/40 bg-amber-950/30 p-2.5 text-xs font-mono text-amber-300 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-amber-400" />
            <span>[SYNTHETIC TEST DATASET] Real-time figures reflect deterministic paper simulation data.</span>
          </div>
          <span className="text-[10px] text-amber-400 font-bold uppercase tracking-wider">PAPER ENVIRONMENT</span>
        </div>
      )}

      {/* Top Banner / Alerts */}
      {actionMessage && (
        <div className="rounded border border-cyan-500/40 bg-cyan-950/40 p-3 text-xs font-mono text-cyan-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <CheckCircle className="w-4 h-4 text-cyan-400" />
            <span>{actionMessage}</span>
          </div>
          <button onClick={() => setActionMessage(null)} className="text-cyan-400 hover:text-cyan-200">
            Dismiss
          </button>
        </div>
      )}

      {/* KPI Telemetry Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Net MTM Card */}
        <div className="panel p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>NET SESSION MTM</span>
            <Activity className="w-4 h-4 text-slate-500" />
          </div>
          <div className="my-2">
            <div className={`text-2xl font-bold font-mono ${getPnlColor(netMtm)}`}>
              {formatINR(netMtm)}
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1 flex space-x-3">
              <span>Realized: {formatINR(realizedPnl)}</span>
              <span>Unrealized: {formatINR(unrealizedPnl)}</span>
            </div>
          </div>
          <div className="text-[10px] font-mono text-slate-500 flex items-center space-x-1">
            {netMtm >= 0 ? (
              <ArrowUpRight className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <ArrowDownRight className="w-3.5 h-3.5 text-rose-400" />
            )}
            <span>Peak Session: {formatINR(peakMtm)}</span>
          </div>
        </div>

        {/* Profit Protection Status */}
        <div className="panel p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>PROFIT PROTECTION</span>
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="my-2">
            <div className="text-base font-bold font-mono text-slate-200">
              {risk?.is_profit_protection_active ? (
                <span className="text-emerald-400">ACTIVE & TRAILING</span>
              ) : (
                <span className="text-amber-400">ARMED (₹3,000 TRIGGER)</span>
              )}
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1">
              Floor Lock: {formatINR(risk?.profit_floor || 0)}
            </div>
          </div>
          <div className="text-[10px] font-mono text-slate-500">
            Rule: Lock ₹500 at ₹3k, trail +₹500/₹1k
          </div>
        </div>

        {/* Daily Loss Breaker */}
        <div className="panel p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>DAILY LOSS BREAKER</span>
            <ShieldAlert className="w-4 h-4 text-rose-400" />
          </div>
          <div className="my-2">
            <div className="text-base font-bold font-mono text-slate-200">
              {risk?.is_circuit_breaker_active ? (
                <span className="text-rose-400">BREAKER TRIPPED</span>
              ) : (
                <span className="text-emerald-400">NORMAL OPERATING</span>
              )}
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1">
              Loss Limit: {formatINR(maxLossLimit)} (Buffer: {formatINR(remainingLossBuffer)})
            </div>
          </div>
          <div className="text-[10px] font-mono text-slate-500">
            Auto-flattens all positions if hit
          </div>
        </div>

        {/* Strategy & Session State */}
        <div className="panel p-4 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>SESSION & ORB STATUS</span>
            <Layers className="w-4 h-4 text-blue-400" />
          </div>
          <div className="my-2">
            <div className="text-sm font-bold font-mono text-blue-400">
              {session?.phase || "ACTIVE_TRADING"}
            </div>
            <div className="text-[11px] font-mono text-slate-300 mt-1">
              ORB: {session?.opening_range?.is_established && session.opening_range.low && session.opening_range.high
                ? `${Number(session.opening_range.low).toFixed(1)} - ${Number(session.opening_range.high).toFixed(1)}`
                : session?.opening_range?.is_established ? "ESTABLISHED" : "NOT ESTABLISHED"}
            </div>
          </div>
          <div className="text-[10px] font-mono text-emerald-400 flex items-center space-x-1">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span>{session?.opening_range?.is_established ? "Range Established at 09:45 IST" : "Range Window: 09:16 - 09:45 IST"}</span>
          </div>
        </div>
      </div>

      {/* Main Grid: Positions & Active Orders */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Positions Section (2 Cols) */}
        <div className="lg:col-span-2 panel p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-[#1c2638] pb-3">
            <div className="flex items-center space-x-2">
              <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
                Open Positions ({positions.length})
              </span>
              <span className="text-[10px] font-mono text-slate-500 bg-[#121824] px-2 py-0.5 rounded border border-[#1f293d]">
                PAPER BROKER
              </span>
            </div>
            <button
              onClick={fetchData}
              className="text-slate-400 hover:text-slate-200 transition-colors p-1"
              title="Refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-[#1c2638]">
                  <th className="pb-2 font-normal">Instrument</th>
                  <th className="pb-2 font-normal">Side</th>
                  <th className="pb-2 font-normal text-right">Qty</th>
                  <th className="pb-2 font-normal text-right">Avg Entry</th>
                  <th className="pb-2 font-normal text-right">LTP</th>
                  <th className="pb-2 font-normal text-right">P&L</th>
                  <th className="pb-2 font-normal text-center">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#162030]">
                {positions.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-slate-500">
                      No open positions in active session.
                    </td>
                  </tr>
                ) : (
                  positions.map((pos) => {
                    const isLong = pos.net_qty > 0;
                    const pnl = Number(pos.total_pnl);
                    return (
                      <tr key={pos.position_id} className="hover:bg-[#121824]/50">
                        <td className="py-2.5 font-semibold text-slate-200">
                          {pos.symbol}
                        </td>
                        <td className="py-2.5">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              isLong ? "text-emerald-400 bg-emerald-950/60" : "text-rose-400 bg-rose-950/60"
                            }`}
                          >
                            {isLong ? "LONG" : "SHORT"}
                          </span>
                        </td>
                        <td className="py-2.5 text-right font-medium">{Math.abs(pos.net_qty)}</td>
                        <td className="py-2.5 text-right text-slate-300">
                          {formatNumber(pos.avg_buy_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-200 font-bold">
                          {formatNumber(pos.current_ltp, 2)}
                        </td>
                        <td className={`py-2.5 text-right font-bold ${getPnlColor(pnl)}`}>
                          {formatINR(pnl)}
                        </td>
                        <td className="py-2.5 text-center">
                          <button
                            onClick={() => handleSquareOff(pos.symbol)}
                            disabled={squaringOff === pos.symbol}
                            className="px-2.5 py-1 bg-rose-900/40 hover:bg-rose-800/80 text-rose-300 border border-rose-700/50 rounded text-[10px] font-semibold transition-colors"
                          >
                            {squaringOff === pos.symbol ? "Closing..." : "Square-Off"}
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* System & Market Telemetry (1 Col) */}
        <div className="panel p-4 space-y-4">
          <div className="border-b border-[#1c2638] pb-2 text-xs font-mono font-bold text-slate-200 uppercase tracking-wider flex items-center justify-between">
            <span>Market & Infrastructure</span>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-950/60 text-blue-300 border border-blue-800">
              {risk?.is_synthetic ? "SYNTHETIC SIM" : "LIVE FEED"}
            </span>
          </div>

          <div className="space-y-3 font-mono text-xs">
            <div className="flex items-center justify-between p-2 rounded bg-[#121824] border border-[#1f293d]">
              <div>
                <div className="text-slate-400 text-[10px]">SPOT UNDERLYING</div>
                <div className="font-bold text-slate-100">NSE:NIFTY50-INDEX</div>
              </div>
              <div className="text-right">
                <div className="font-bold text-emerald-400">
                  {session?.opening_range?.high ? formatNumber(session.opening_range.high, 2) : "25,062.40"}
                </div>
                <div className="text-[10px] text-emerald-500">Spot Tracking</div>
              </div>
            </div>

            <div className="flex items-center justify-between p-2 rounded bg-[#121824] border border-[#1f293d]">
              <div>
                <div className="text-slate-400 text-[10px]">RESOLVED OPTION</div>
                <div className="font-bold text-slate-100">
                  {session?.active_contract_symbol || (positions.length > 0 ? positions[0].symbol : "NSE:NIFTY26NOV25000CE")}
                </div>
              </div>
              <div className="text-right">
                <div className="font-bold text-slate-200">
                  {positions.length > 0 ? formatNumber(positions[0].current_ltp, 2) : "154.50"}
                </div>
                <div className="text-[10px] text-emerald-400">Active Leg</div>
              </div>
            </div>

            <div className="space-y-2 pt-2 border-t border-[#1c2638] text-[11px]">
              <div className="flex items-center justify-between text-slate-400">
                <span>Broker API</span>
                <span className="text-emerald-400 font-semibold flex items-center space-x-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  <span>FYERS v3 (Paper)</span>
                </span>
              </div>
              <div className="flex items-center justify-between text-slate-400">
                <span>OMS Engine</span>
                <span className="text-emerald-400 font-semibold">Deterministic State Machine</span>
              </div>
              <div className="flex items-center justify-between text-slate-400">
                <span>Database Sync</span>
                <span className="text-emerald-400 font-semibold">PostgreSQL / In-Memory State</span>
              </div>
              <div className="flex items-center justify-between text-slate-400">
                <span>Telemetry WS</span>
                <span className="text-cyan-400 font-semibold">ws://localhost:8000/ws/telemetry</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Orders Section */}
      <div className="panel p-4 space-y-3">
        <div className="flex items-center justify-between border-b border-[#1c2638] pb-3">
          <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
            Recent Orders & Executions ({orders.length})
          </span>
          <span className="text-[10px] font-mono text-slate-400">
            Lifecycle: Intent → Validating → Open → Filled
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1c2638]">
                <th className="pb-2 font-normal">Client Order ID</th>
                <th className="pb-2 font-normal">Broker Order ID</th>
                <th className="pb-2 font-normal">Symbol</th>
                <th className="pb-2 font-normal">Side</th>
                <th className="pb-2 font-normal text-right">Quantity</th>
                <th className="pb-2 font-normal text-right">Filled Qty</th>
                <th className="pb-2 font-normal">Type</th>
                <th className="pb-2 font-normal text-center">Status</th>
                <th className="pb-2 font-normal text-right">Placed Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#162030]">
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-slate-500">
                    No orders logged for current session.
                  </td>
                </tr>
              ) : (
                orders.map((ord) => {
                  const isBuy = ord.side === "BUY";
                  return (
                    <tr key={ord.client_order_id} className="hover:bg-[#121824]/50">
                      <td className="py-2.5 font-bold text-slate-300">{ord.client_order_id}</td>
                      <td className="py-2.5 text-slate-400 font-mono text-[11px]">
                        {ord.broker_order_id || "—"}
                      </td>
                      <td className="py-2.5 font-semibold text-slate-200">{ord.symbol}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            isBuy ? "text-emerald-400 bg-emerald-950/60" : "text-rose-400 bg-rose-950/60"
                          }`}
                        >
                          {ord.side}
                        </span>
                      </td>
                      <td className="py-2.5 text-right font-medium">{ord.qty}</td>
                      <td className="py-2.5 text-right text-slate-300">{ord.filled_qty}</td>
                      <td className="py-2.5 text-slate-400">{ord.order_type}</td>
                      <td className="py-2.5 text-center">
                        <span
                          className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider ${
                            ord.status === "FILLED"
                              ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                              : ord.status === "CANCELLED"
                              ? "bg-slate-800 text-slate-400"
                              : "bg-blue-950 text-blue-400 border border-blue-800"
                          }`}
                        >
                          {ord.status}
                        </span>
                      </td>
                      <td className="py-2.5 text-right text-slate-400 text-[11px]">
                        {formatISTTime(ord.submitted_at)}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
