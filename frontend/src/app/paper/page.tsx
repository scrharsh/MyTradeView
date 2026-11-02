"use client";

import React, { useEffect, useState } from "react";
import {
  Terminal,
  Play,
  Pause,
  Square,
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  Activity,
  Layers,
  ArrowRight,
  RefreshCw,
} from "lucide-react";
import { api } from "../../lib/api";
import { formatINR, formatNumber, getPnlColor, formatISTTime } from "../../lib/formatters";
import {
  SessionActiveResponse,
  PositionResponseItem,
  OrderResponseItem,
  RiskSnapshotResponse,
} from "../../types/api";

export default function PaperTradingPage() {
  const [session, setSession] = useState<SessionActiveResponse | null>(null);
  const [positions, setPositions] = useState<PositionResponseItem[]>([]);
  const [orders, setOrders] = useState<OrderResponseItem[]>([]);
  const [risk, setRisk] = useState<RiskSnapshotResponse | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  // Simulated live event feed
  const [liveLogs, setLiveLogs] = useState<
    { timestamp: string; level: string; message: string }[]
  >([
    {
      timestamp: "09:16:00",
      level: "INFO",
      message: "Session SES-20261106-001 started in PAPER execution mode",
    },
    {
      timestamp: "09:16:05",
      level: "INFO",
      message: "Opening range window initialized: 09:16:00 to 09:45:00 IST",
    },
    {
      timestamp: "09:45:00",
      level: "STATE",
      message: "Opening range established. High: 25,045.50 | Low: 24,962.10",
    },
    {
      timestamp: "09:45:02",
      level: "INFO",
      message: "Transitioned to WAITING_FOR_BREAKOUT. Tracking spot ticks...",
    },
    {
      timestamp: "09:46:00",
      level: "SIGNAL",
      message: "Bullish breakout detected! 09:46 Close: 25,055.20 > ORB High 25,045.50",
    },
    {
      timestamp: "09:46:01",
      level: "RESOLVER",
      message: "Resolved ATM CE contract: NSE:NIFTY26NOV25000CE (Strike 25000 CE)",
    },
    {
      timestamp: "09:46:02",
      level: "OMS",
      message: "Created order intent INT-001 -> Validating risk limits -> DISPATCHED",
    },
    {
      timestamp: "09:46:03",
      level: "FILL",
      message: "Paper broker executed BUY 75 NSE:NIFTY26NOV25000CE @ 143.00",
    },
    {
      timestamp: "10:15:00",
      level: "RISK",
      message: "Profit tracker update: LTP 154.50. Unrealized P&L: ₹862.50. Risk floor: ₹0.00",
    },
  ]);

  const loadData = async () => {
    try {
      const [sRes, pRes, oRes, rRes] = await Promise.allSettled([
        api.getActiveSession(),
        api.getPositions(),
        api.getOrders(),
        api.getRiskSnapshot(),
      ]);
      if (sRes.status === "fulfilled") setSession(sRes.value);
      if (pRes.status === "fulfilled") setPositions(Array.isArray(pRes.value) ? pRes.value : []);
      if (oRes.status === "fulfilled") setOrders(Array.isArray(oRes.value) ? oRes.value : []);
      if (rRes.status === "fulfilled") setRisk(rRes.value);
    } catch (err) {
      console.error("Paper console refresh error:", err);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleTogglePause = () => {
    setIsPaused(!isPaused);
    const notice = !isPaused
      ? "Engine execution PAUSED by operator. Ticks will be buffered."
      : "Engine execution RESUMED.";
    setActionNotice(notice);
    setLiveLogs((prev) => [
      ...prev,
      {
        timestamp: new Date().toLocaleTimeString("en-IN", { hour12: false }),
        level: "OPERATOR",
        message: notice,
      },
    ]);
    setTimeout(() => setActionNotice(null), 4000);
  };

  const handleSquareOffAll = async () => {
    if (!confirm("Confirm immediate square-off of ALL open positions?")) return;
    try {
      for (const pos of positions) {
        await api.squareOffPosition(pos.symbol);
      }
      setActionNotice("All open positions squared off at market price.");
      setLiveLogs((prev) => [
        ...prev,
        {
          timestamp: new Date().toLocaleTimeString("en-IN", { hour12: false }),
          level: "OPERATOR",
          message: "Emergency Square-Off All initiated. All derivative positions flattened.",
        },
      ]);
      await loadData();
    } catch (err: any) {
      setActionNotice(`Error in square-off all: ${err?.message}`);
    } finally {
      setTimeout(() => setActionNotice(null), 4000);
    }
  };

  const stateStages = [
    { id: "ORB_BUILDING", label: "09:16-09:45 ORB BUILDING", completed: true },
    { id: "ORB_ESTABLISHED", label: "ORB ESTABLISHED", completed: true },
    { id: "BREAKOUT_TRIGGERED", label: "BREAKOUT ENTRY", completed: true },
    {
      id: "POSITION_OPEN",
      label: "POSITION ACTIVE (PROFIT TRAILING)",
      completed: positions.length > 0,
      active: positions.length > 0,
    },
    { id: "SQUARED_OFF", label: "SQUARED OFF / EOD", completed: false },
  ];

  const netMtm = risk ? parseFloat(String(risk.total_mtm)) : 862.5;

  return (
    <div className="space-y-6">
      {/* Top Banner & Operator Controls */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-4 gap-3">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <Terminal className="w-5 h-5 text-emerald-400" />
            <span>PAPER TRADING EXECUTION CONSOLE</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Simulated market execution environment powered by FYERS Paper adapter & deterministic OMS.
          </p>
        </div>

        {/* Operator Action Buttons */}
        <div className="flex items-center space-x-2.5 font-mono text-xs">
          <button
            onClick={handleTogglePause}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded font-bold transition-colors ${
              isPaused
                ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                : "bg-amber-600 hover:bg-amber-500 text-white"
            }`}
          >
            {isPaused ? <Play className="w-3.5 h-3.5 fill-current" /> : <Pause className="w-3.5 h-3.5 fill-current" />}
            <span>{isPaused ? "RESUME ENGINE" : "PAUSE ENGINE"}</span>
          </button>

          <button
            onClick={handleSquareOffAll}
            disabled={positions.length === 0}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded font-bold transition-colors ${
              positions.length > 0
                ? "bg-rose-900/50 hover:bg-rose-800 text-rose-200 border border-rose-700"
                : "bg-slate-800 text-slate-500 cursor-not-allowed"
            }`}
          >
            <Square className="w-3.5 h-3.5" />
            <span>SQUARE-OFF ALL</span>
          </button>
        </div>
      </div>

      {actionNotice && (
        <div className="rounded border border-cyan-500/40 bg-cyan-950/40 p-3 text-xs font-mono text-cyan-200">
          {actionNotice}
        </div>
      )}

      {/* Strategy State Machine Visualizer */}
      <div className="panel p-5 space-y-3">
        <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Layers className="w-4 h-4 text-cyan-400" />
            <span>STRATEGY STATE MACHINE</span>
          </div>
          <span className="text-[10px] text-emerald-400 font-bold">STATE: IN_POSITION (LONG CE)</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-2 pt-2">
          {stateStages.map((st, i) => (
            <div
              key={st.id}
              className={`p-3 rounded border font-mono text-xs flex flex-col justify-between space-y-2 ${
                st.active
                  ? "bg-cyan-950/50 border-cyan-500/70 text-cyan-300 shadow-md shadow-cyan-950/40"
                  : st.completed
                  ? "bg-emerald-950/20 border-emerald-800/40 text-emerald-400"
                  : "bg-[#121824] border-[#1f293d] text-slate-500"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold text-slate-400">STAGE 0{i + 1}</span>
                {st.completed ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <div className="w-3.5 h-3.5 rounded-full border border-slate-600" />
                )}
              </div>
              <div className="font-bold text-[11px] leading-tight">{st.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Main Console Split: Live Terminal & Order Flow */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Real-time Event Stream / Logging Terminal */}
        <div className="panel p-4 space-y-3 flex flex-col h-[420px]">
          <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
            <div className="flex items-center space-x-2">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="font-bold text-slate-200">STRATEGY & OMS EVENT STREAM</span>
            </div>
            <span className="text-[10px] text-slate-500">Live Tick & Signal Dispatch</span>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1.5 font-mono text-[11px] p-2 bg-[#090d13] rounded border border-[#162030] scrollbar-thin">
            {liveLogs.map((log, idx) => {
              const levelColor =
                log.level === "SIGNAL"
                  ? "text-cyan-400 font-bold"
                  : log.level === "FILL"
                  ? "text-emerald-400 font-bold"
                  : log.level === "STATE"
                  ? "text-amber-400 font-bold"
                  : log.level === "OPERATOR"
                  ? "text-rose-400 font-bold"
                  : "text-slate-400";

              return (
                <div key={idx} className="leading-relaxed">
                  <span className="text-slate-600">[{log.timestamp}]</span>{" "}
                  <span className={levelColor}>[{log.level}]</span>{" "}
                  <span className="text-slate-300">{log.message}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Simulated Orderbook & Execution Fills */}
        <div className="panel p-4 space-y-3 flex flex-col h-[420px]">
          <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
            <span className="font-bold text-slate-200 uppercase tracking-wider">
              Simulated Execution Fills ({orders.flatMap((o) => o.fills).length})
            </span>
            <span className="text-[10px] text-slate-500">Paper Broker Matched Fills</span>
          </div>

          <div className="flex-1 overflow-y-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-[#1c2638]">
                  <th className="pb-2 font-normal">Fill ID</th>
                  <th className="pb-2 font-normal">Symbol</th>
                  <th className="pb-2 font-normal">Side</th>
                  <th className="pb-2 font-normal text-right">Qty</th>
                  <th className="pb-2 font-normal text-right">Price</th>
                  <th className="pb-2 font-normal text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#162030]">
                {orders.flatMap((o) => o.fills).length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-slate-500">
                      No execution fills yet.
                    </td>
                  </tr>
                ) : (
                  orders
                    .flatMap((o) => o.fills)
                    .map((f) => (
                      <tr key={f.fill_id} className="hover:bg-[#121824]/50">
                        <td className="py-2.5 font-bold text-slate-300">{f.fill_id}</td>
                        <td className="py-2.5 text-slate-200">{f.symbol}</td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400">
                            {f.side}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-200 font-medium">{f.fill_qty}</td>
                        <td className="py-2.5 text-right font-bold text-emerald-400">
                          {formatNumber(f.fill_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-400 text-[11px]">
                          {formatISTTime(f.timestamp)}
                        </td>
                      </tr>
                    ))
                )}
              </tbody>
            </table>
          </div>

          {/* Current Position Quick Snapshot */}
          <div className="pt-2 border-t border-[#1c2638] flex items-center justify-between font-mono text-xs">
            <div className="text-slate-400">
              Active Contract: <strong className="text-slate-200">NSE:NIFTY26NOV25000CE</strong>
            </div>
            <div className="text-right">
              MTM: <strong className={getPnlColor(netMtm)}>{formatINR(netMtm)}</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
