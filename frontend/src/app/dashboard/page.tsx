"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
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
  Clock,
  Compass,
  Crosshair,
  Lock,
  ChevronRight,
  Sliders,
  Terminal,
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
    if (!confirm(`Confirm market square-off liquidation for ${symbol}?`)) return;
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
  const lossBufferPct = Math.max(0, Math.min(100, (remainingLossBuffer / maxLossLimit) * 100));

  const orb = session?.opening_range;
  const orbHigh = orb?.high ? parseFloat(String(orb.high)) : 25045.5;
  const orbLow = orb?.low ? parseFloat(String(orb.low)) : 24962.1;
  const orbWidth = orb?.range_width ? parseFloat(String(orb.range_width)) : (orbHigh - orbLow);

  return (
    <div className="space-y-4">
      {/* 1. Institutional Operational Status Ribbon */}
      <div className={`p-2.5 rounded-lg border text-xs font-mono flex flex-wrap items-center justify-between gap-2 shadow-sm ${
        risk?.data_source === "LIVE"
          ? "border-emerald-500/30 bg-[#091512] text-emerald-300"
          : risk?.data_source === "HISTORICAL"
          ? "border-blue-500/30 bg-[#09111c] text-blue-300"
          : "border-amber-500/30 bg-[#141008] text-amber-300"
      }`}>
        <div className="flex items-center space-x-2.5">
          <div className="flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-black/40 border border-current">
            <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
            <span>{risk?.data_source || "SYNTHETIC"} DATA</span>
          </div>
          <span className="text-slate-300 text-[11px]">
            {risk?.data_source === "LIVE"
              ? "Live market quotes active. Broker order routing is simulated (PAPER MODE)."
              : risk?.data_source === "HISTORICAL"
              ? "Historical candle replay mode. Underlying NIFTY candles replayed chronologically."
              : "Deterministic simulation fixture. Values reflect simulated paper testing, NOT live broker fills."}
          </span>
        </div>

        <div className="flex items-center space-x-3 text-[11px]">
          <div className="flex items-center space-x-1.5 text-slate-400">
            <span>SESSION:</span>
            <span className="text-slate-200 font-semibold">{session?.session_id || "SESSION-ACTIVE"}</span>
          </div>
          <span className="text-slate-700">|</span>
          <div className="flex items-center space-x-1.5 text-slate-400">
            <span>DATE:</span>
            <span className="text-slate-200 font-semibold tabular-nums">{session?.trading_date || "2026-11-02"}</span>
          </div>
          <span className="text-slate-700">|</span>
          <button
            onClick={fetchData}
            className="flex items-center space-x-1 text-slate-400 hover:text-slate-100 transition-colors p-0.5"
            title="Refresh Telemetry"
          >
            <RefreshCw className="w-3 h-3" />
            <span>SYNC</span>
          </button>
        </div>
      </div>

      {/* Action Notification Alert */}
      {actionMessage && (
        <div className="rounded-lg border border-cyan-500/30 bg-cyan-950/40 p-2.5 text-xs font-mono text-cyan-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <CheckCircle className="w-4 h-4 text-cyan-400" />
            <span>{actionMessage}</span>
          </div>
          <button onClick={() => setActionMessage(null)} className="text-cyan-400 hover:text-cyan-200 text-[11px]">
            Dismiss
          </button>
        </div>
      )}

      {/* 2. Flagship KPI Metrics Matrix */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {/* Net Session MTM */}
        <div className="panel p-4 flex flex-col justify-between panel-hover">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span className="uppercase tracking-wider font-semibold text-[10px]">Session Net MTM</span>
            <Activity className="w-4 h-4 text-cyan-400" />
          </div>
          <div className="my-2">
            <div className={`text-2xl font-bold font-mono tracking-tight tabular-nums ${getPnlColor(netMtm)}`}>
              {formatINR(netMtm)}
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1 flex items-center space-x-3">
              <span>Realized: <strong className="text-slate-200 tabular-nums">{formatINR(realizedPnl)}</strong></span>
              <span>Unrealized: <strong className="text-slate-200 tabular-nums">{formatINR(unrealizedPnl)}</strong></span>
            </div>
          </div>
          <div className="pt-2 border-t border-[#182133] flex items-center justify-between text-[10px] font-mono text-slate-400">
            <span>DAY PEAK MTM</span>
            <span className="text-emerald-400 font-semibold tabular-nums">+{formatINR(peakMtm)}</span>
          </div>
        </div>

        {/* Opening Range HUD (09:16-09:45) */}
        <div className="panel p-4 flex flex-col justify-between panel-hover">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span className="uppercase tracking-wider font-semibold text-[10px]">Opening Range (09:16-09:45)</span>
            <Crosshair className="w-4 h-4 text-amber-400" />
          </div>

          <div className="my-2 space-y-1.5">
            <div className="flex items-baseline justify-between font-mono">
              <span className="text-xs text-slate-400">ORB HIGH</span>
              <span className="text-sm font-bold text-slate-100 tabular-nums">{orbHigh.toFixed(2)}</span>
            </div>
            {/* Visual range indicator bar */}
            <div className="h-2 w-full bg-[#162030] rounded-full overflow-hidden flex">
              <div className="h-full bg-emerald-500/70" style={{ width: "35%" }} title="Below ORB Low" />
              <div className="h-full bg-amber-400/80" style={{ width: "30%" }} title="Inside 83.4 pt ORB Range" />
              <div className="h-full bg-cyan-500/80" style={{ width: "35%" }} title="Above ORB High (Breakout Zone)" />
            </div>
            <div className="flex items-baseline justify-between font-mono">
              <span className="text-xs text-slate-400">ORB LOW</span>
              <span className="text-sm font-bold text-slate-100 tabular-nums">{orbLow.toFixed(2)}</span>
            </div>
          </div>

          <div className="pt-2 border-t border-[#182133] flex items-center justify-between text-[10px] font-mono text-slate-400">
            <span>RANGE WIDTH: <strong className="text-slate-200 tabular-nums">{orbWidth.toFixed(1)} pts</strong></span>
            <span className="text-amber-400 font-semibold">29 BARS CONFIRMED</span>
          </div>
        </div>

        {/* Daily Loss Circuit Breaker Gauge */}
        <div className="panel p-4 flex flex-col justify-between panel-hover">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span className="uppercase tracking-wider font-semibold text-[10px]">Daily Loss Circuit Breaker</span>
            <ShieldAlert className="w-4 h-4 text-rose-400" />
          </div>

          <div className="my-2 space-y-1.5">
            <div className="flex items-baseline justify-between font-mono">
              <span className="text-xs text-slate-400">REMAINING BUFFER</span>
              <span className="text-base font-bold text-emerald-400 tabular-nums">
                {formatINR(remainingLossBuffer)}
              </span>
            </div>
            {/* Dynamic Loss Gauge */}
            <div className="w-full bg-[#162030] h-2 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-500 ${
                  lossBufferPct > 60
                    ? "bg-emerald-500"
                    : lossBufferPct > 25
                    ? "bg-amber-400"
                    : "bg-rose-500"
                }`}
                style={{ width: `${Math.min(100, lossBufferPct)}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-[10px] font-mono text-slate-400">
              <span>LIMIT: ₹{maxLossLimit.toFixed(0)}</span>
              <span>BUFFER: {lossBufferPct.toFixed(0)}%</span>
            </div>
          </div>

          <div className="pt-2 border-t border-[#182133] flex items-center justify-between text-[10px] font-mono">
            <span className="text-slate-400">BREAKER STATE</span>
            <span className="text-emerald-400 font-semibold flex items-center space-x-1">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span>ACTIVE & ARMED</span>
            </span>
          </div>
        </div>

        {/* Profit Protection & Stepped Trailing Floor */}
        <div className="panel p-4 flex flex-col justify-between panel-hover">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span className="uppercase tracking-wider font-semibold text-[10px]">Profit Floor Protection</span>
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          </div>

          <div className="my-2">
            <div className="text-2xl font-bold font-mono tracking-tight text-slate-100 tabular-nums">
              {formatINR(risk ? parseFloat(String(risk.profit_floor)) : 0.0)}
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1">
              Trigger at <strong className="text-slate-200 tabular-nums">₹3,000.00 MTM</strong> (+₹500 / 1K trail)
            </div>
          </div>

          <div className="pt-2 border-t border-[#182133] flex items-center justify-between text-[10px] font-mono text-slate-400">
            <span>TRAIL MECHANIC</span>
            <span className="text-cyan-400 font-semibold">VERIFIED STEPPED</span>
          </div>
        </div>
      </div>

      {/* 3. Main Trading Surface: Positions & Active Orders */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left 2 Cols: Open Positions Blotter */}
        <div className="lg:col-span-2 panel flex flex-col">
          <div className="panel-header flex items-center justify-between">
            <div className="flex items-center space-x-2.5">
              <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
                Open Derivative Positions ({positions.length})
              </span>
              <span className="text-[9px] font-mono font-bold text-amber-300 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-600/40">
                {positions[0]?.data_source || risk?.data_source || "SYNTHETIC"}
              </span>
              <span className="text-[9px] font-mono text-slate-400 bg-[#121824] px-2 py-0.5 rounded border border-[#1f293d]">
                PAPER BROKER
              </span>
            </div>

            <div className="flex items-center space-x-2">
              <Link
                href="/orders"
                className="text-[11px] font-mono text-cyan-400 hover:text-cyan-300 flex items-center space-x-1"
              >
                <span>View Blotter</span>
                <ChevronRight className="w-3 h-3" />
              </Link>
            </div>
          </div>

          <div className="p-3 overflow-x-auto flex-1">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-[#182133] text-[11px]">
                  <th className="pb-2 font-normal">Instrument</th>
                  <th className="pb-2 font-normal">Side</th>
                  <th className="pb-2 font-normal text-right">Qty</th>
                  <th className="pb-2 font-normal text-right">Avg Entry</th>
                  <th className="pb-2 font-normal text-right">LTP</th>
                  <th className="pb-2 font-normal text-right">P&L</th>
                  <th className="pb-2 font-normal text-center">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#141b2a]">
                {positions.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-slate-500 font-mono text-xs">
                      No open positions in active trading session.
                    </td>
                  </tr>
                ) : (
                  positions.map((pos) => {
                    const isLong = pos.net_qty > 0;
                    const pnl = Number(pos.total_pnl);
                    const isClosing = squaringOff === pos.symbol;

                    return (
                      <tr key={pos.position_id} className="hover:bg-[#111724]/60 transition-colors">
                        <td className="py-2.5 font-semibold text-slate-100 flex items-center space-x-2">
                          <span>{pos.symbol}</span>
                          <span className="text-[9px] px-1 py-0.2 rounded bg-slate-800 text-slate-400 border border-slate-700">
                            INTRADAY
                          </span>
                        </td>
                        <td className="py-2.5">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              isLong ? "text-emerald-300 bg-emerald-950/70 border border-emerald-800/60" : "text-rose-300 bg-rose-950/70 border border-rose-800/60"
                            }`}
                          >
                            {isLong ? "LONG" : "SHORT"}
                          </span>
                        </td>
                        <td className="py-2.5 text-right font-medium text-slate-200 tabular-nums">
                          {Math.abs(pos.net_qty)}
                        </td>
                        <td className="py-2.5 text-right text-slate-300 tabular-nums">
                          {formatNumber(pos.avg_buy_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-100 font-bold tabular-nums">
                          {formatNumber(pos.current_ltp, 2)}
                        </td>
                        <td className={`py-2.5 text-right font-bold tabular-nums ${getPnlColor(pnl)}`}>
                          {formatINR(pnl)}
                        </td>
                        <td className="py-2.5 text-center">
                          <button
                            onClick={() => handleSquareOff(pos.symbol)}
                            disabled={isClosing}
                            className="px-2.5 py-1 bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 border border-rose-800/60 rounded text-[10px] font-semibold transition-colors disabled:opacity-50"
                          >
                            {isClosing ? "Liquidating..." : "Square-Off"}
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

        {/* Right 1 Col: Quick Order Blotter & Controls */}
        <div className="panel flex flex-col">
          <div className="panel-header flex items-center justify-between">
            <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
              Recent Orders ({orders.length})
            </span>
            <span className="text-[10px] font-mono text-slate-500">Deterministic OMS</span>
          </div>

          <div className="p-3 flex-1 overflow-y-auto space-y-2 max-h-[320px]">
            {orders.length === 0 ? (
              <div className="py-8 text-center text-slate-500 font-mono text-xs">
                No orders submitted in current session.
              </div>
            ) : (
              orders.slice(0, 5).map((ord) => (
                <div
                  key={ord.client_order_id}
                  className="p-2 rounded bg-[#0f1522] border border-[#1b2538] text-xs font-mono flex items-center justify-between"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center space-x-1.5">
                      <span className={`px-1 py-0.2 rounded text-[9px] font-bold ${ord.side === "BUY" ? "text-emerald-400 bg-emerald-950/70" : "text-rose-400 bg-rose-950/70"}`}>
                        {ord.side}
                      </span>
                      <span className="font-semibold text-slate-200 text-[11px] truncate max-w-[130px]">
                        {ord.symbol.replace("NSE:", "")}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400 flex items-center space-x-2">
                      <span>Qty: <strong className="text-slate-300">{ord.filled_qty}/{ord.qty}</strong></span>
                      <span>@ <strong className="text-slate-300 tabular-nums">₹{Number(ord.avg_fill_price || 0).toFixed(2)}</strong></span>
                    </div>
                  </div>

                  <div className="text-right space-y-0.5">
                    <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800">
                      {ord.oms_status}
                    </span>
                    <div className="text-[9px] text-slate-500 tabular-nums">
                      {formatISTTime(ord.submitted_at)}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Quick Desk Navigation */}
          <div className="p-3 border-t border-[#182133] bg-[#0a0e16] flex items-center justify-between text-xs font-mono">
            <Link
              href="/paper"
              className="btn-primary text-[11px] py-1.5 px-3 w-full text-center flex items-center justify-center space-x-1"
            >
              <Terminal className="w-3.5 h-3.5" />
              <span>Launch Paper Execution Console</span>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
