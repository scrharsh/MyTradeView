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
  Clock,
  Zap,
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

  // Live event feed from persistent audit events
  const [liveLogs, setLiveLogs] = useState<
    { timestamp: string; level: string; message: string }[]
  >([]);

  const loadData = async () => {
    try {
      const [sRes, pRes, oRes, rRes, aRes] = await Promise.allSettled([
        api.getActiveSession(),
        api.getPositions(),
        api.getOrders(),
        api.getRiskSnapshot(),
        api.getAuditEvents(50),
      ]);
      if (sRes.status === "fulfilled") setSession(sRes.value);
      if (pRes.status === "fulfilled") setPositions(Array.isArray(pRes.value) ? pRes.value : []);
      if (oRes.status === "fulfilled") setOrders(Array.isArray(oRes.value) ? oRes.value : []);
      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (aRes.status === "fulfilled" && Array.isArray(aRes.value)) {
        const events = aRes.value.map((evt) => {
          const detailStr = typeof evt.details === "object" ? JSON.stringify(evt.details) : String(evt.details || "");
          return {
            timestamp: formatISTTime(evt.timestamp),
            level: evt.event_type || "EVENT",
            message: `${evt.component || evt.actor || "SYSTEM"}: ${detailStr}`,
          };
        });
        setLiveLogs(events);
      }
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
      ? "Engine execution PAUSED by operator. Ticks buffered."
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

  const strategyState = session?.strategy_state || (positions.length > 0 ? "IN_POSITION" : "WAITING_FOR_ORB");
  const isOrbEst = session?.opening_range?.is_established || false;
  const hasPositions = positions.length > 0;
  const isSquaredOff = !hasPositions && orders.length > 0 && orders.some((o) => o.side === "SELL");

  const stateStages = [
    { id: "ORB_BUILDING", num: "01", label: "09:16-09:45 ORB FORMATION", completed: isOrbEst, active: !isOrbEst },
    { id: "ORB_ESTABLISHED", num: "02", label: "ORB BOUNDARIES LOCKED", completed: isOrbEst, active: isOrbEst && !hasPositions && !isSquaredOff },
    { id: "BREAKOUT_TRIGGERED", num: "03", label: "BREAKOUT ENTRY ORDER", completed: hasPositions || isSquaredOff, active: false },
    {
      id: "POSITION_OPEN",
      num: "04",
      label: "POSITION ACTIVE (TRAILING)",
      completed: hasPositions,
      active: hasPositions,
    },
    { id: "SQUARED_OFF", num: "05", label: "SQUARED OFF / EOD", completed: isSquaredOff, active: isSquaredOff },
  ];

  const netMtm = risk ? parseFloat(String(risk.total_mtm)) : 0.0;

  return (
    <div className="space-y-5">
      {/* Top Banner & Operator Controls */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>TRADING & EXECUTION</span>
            <span>/</span>
            <span>SIMULATED BROKER GATEWAY</span>
            <span>/</span>
            <span className="text-emerald-400">PAPER EXECUTION CONSOLE</span>
          </div>
          <div className="flex items-center space-x-3">
            <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
              <Terminal className="w-5 h-5 text-emerald-400" />
              <span>PAPER TRADING EXECUTION CONSOLE</span>
            </h1>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase border ${
                session?.data_source === "LIVE"
                  ? "bg-emerald-950/70 border-emerald-500/50 text-emerald-300"
                  : session?.data_source === "HISTORICAL"
                  ? "bg-blue-950/70 border-blue-500/50 text-blue-300"
                  : "bg-amber-950/70 border-amber-500/50 text-amber-300"
              }`}
            >
              DATA: {session?.data_source || "SYNTHETIC"}
            </span>
          </div>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Simulated paper execution environment (Session: {session?.session_id || "ACTIVE"} | Date: {session?.trading_date || "2026-11-02"}).
          </p>
        </div>

        {/* Operator Action Buttons */}
        <div className="flex items-center space-x-2 font-mono text-xs">
          <button
            onClick={handleTogglePause}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded font-bold transition-all ${
              isPaused
                ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-950/50"
                : "bg-amber-600 hover:bg-amber-500 text-white shadow-md shadow-amber-950/50"
            }`}
          >
            {isPaused ? <Play className="w-3.5 h-3.5 fill-current" /> : <Pause className="w-3.5 h-3.5 fill-current" />}
            <span>{isPaused ? "RESUME ENGINE" : "PAUSE ENGINE"}</span>
          </button>

          <button
            onClick={handleSquareOffAll}
            disabled={positions.length === 0}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded font-bold transition-all ${
              positions.length > 0
                ? "btn-danger shadow-md shadow-rose-950/50"
                : "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
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
      <div className="panel p-4 space-y-3">
        <div className="panel-header">
          <div className="flex items-center space-x-2">
            <Layers className="w-4 h-4 text-cyan-400" />
            <span>ORB STRATEGY STATE MACHINE</span>
          </div>
          <div className="flex items-center space-x-2 font-mono text-xs">
            <span className="text-slate-400">STATE:</span>
            <span className="text-emerald-400 font-bold bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-800/60">
              {strategyState}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-2 pt-1 font-mono text-xs">
          {stateStages.map((st) => (
            <div
              key={st.id}
              className={`p-3 rounded border flex flex-col justify-between space-y-2 transition-all ${
                st.active
                  ? "bg-[#101b2b] border-cyan-500/70 text-cyan-300 shadow-md shadow-cyan-950/40"
                  : st.completed
                  ? "bg-emerald-950/20 border-emerald-800/40 text-emerald-400"
                  : "bg-[#090d14] border-[#1a2336] text-slate-500"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold text-slate-400">STAGE {st.num}</span>
                {st.completed ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                ) : st.active ? (
                  <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                ) : (
                  <div className="w-3 h-3 rounded-full border border-slate-700" />
                )}
              </div>
              <div className="font-bold text-[11px] leading-tight">{st.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Main Console Split: Live Terminal & Order Flow */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Left: Real-time Event Stream / Logging Terminal */}
        <div className="panel p-4 space-y-3 flex flex-col h-[440px]">
          <div className="panel-header">
            <div className="flex items-center space-x-2">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              <span>STRATEGY & OMS EVENT STREAM</span>
            </div>
            <span className="text-[10px] text-slate-400 font-mono">
              Event Dispatch ({session?.data_source || "SYNTHETIC"})
            </span>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1.5 font-mono text-[11px] p-3 bg-[#06080d] rounded border border-[#141b29] scrollbar-thin">
            {liveLogs.length === 0 ? (
              <div className="text-slate-600 text-center py-8">
                Awaiting strategy signals and broker dispatch events...
              </div>
            ) : (
              liveLogs.map((log, idx) => {
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
                    <span className="text-slate-600 tabular-nums">[{log.timestamp}]</span>{" "}
                    <span className={levelColor}>[{log.level}]</span>{" "}
                    <span className="text-slate-300">{log.message}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Simulated Execution Fills Blotter */}
        <div className="panel p-4 space-y-3 flex flex-col h-[440px]">
          <div className="panel-header">
            <span className="uppercase tracking-wider">
              Simulated Execution Fills ({orders.flatMap((o) => o.fills).length})
            </span>
            <span className="text-[10px] text-slate-500 font-mono">Paper Broker Matched Fills</span>
          </div>

          <div className="flex-1 overflow-y-auto scrollbar-thin">
            <table className="w-full text-left font-mono text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-[#1a2336] text-[11px]">
                  <th className="pb-2 font-semibold">Fill ID</th>
                  <th className="pb-2 font-semibold">Contract</th>
                  <th className="pb-2 font-semibold">Side</th>
                  <th className="pb-2 font-semibold text-right">Qty</th>
                  <th className="pb-2 font-semibold text-right">Fill Price</th>
                  <th className="pb-2 font-semibold text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#131b2a]">
                {orders.flatMap((o) => o.fills).length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-slate-500">
                      No execution fills yet in this session.
                    </td>
                  </tr>
                ) : (
                  orders
                    .flatMap((o) => o.fills)
                    .map((f) => (
                      <tr key={f.fill_id} className="hover:bg-[#0d121c]/60 transition-colors">
                        <td className="py-2.5 font-bold text-slate-300">{f.fill_id}</td>
                        <td className="py-2.5 font-semibold text-slate-200">{f.symbol}</td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950/70 border border-emerald-800 text-emerald-400">
                            {f.side}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-200 font-medium tabular-nums">{f.fill_qty}</td>
                        <td className="py-2.5 text-right font-bold text-emerald-400 tabular-nums">
                          ₹{formatNumber(f.fill_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-400 text-[11px] tabular-nums">
                          {formatISTTime(f.timestamp)}
                        </td>
                      </tr>
                    ))
                )}
              </tbody>
            </table>
          </div>

          {/* Current Position Quick Snapshot Footer */}
          <div className="pt-2 border-t border-[#1a2336] flex items-center justify-between font-mono text-xs">
            <div className="text-slate-400">
              Active Contract:{" "}
              <strong className="text-slate-200">
                {session?.active_contract_symbol || (positions.length > 0 ? positions[0].symbol : "None")}
              </strong>
            </div>
            <div className="text-right">
              MTM: <strong className={`tabular-nums ${getPnlColor(netMtm)}`}>{risk ? formatINR(netMtm) : "--"}</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
