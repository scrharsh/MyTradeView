"use client";

import React, { useEffect, useState } from "react";
import {
  Activity,
  ShieldAlert,
  Wifi,
  Database,
  Clock,
  CheckCircle2,
  TrendingUp,
  Cpu,
  Lock,
} from "lucide-react";
import { formatINR, getPnlColor } from "../lib/formatters";
import { api } from "../lib/api";
import { RiskSnapshotResponse, SessionActiveResponse, SystemHealthResponse } from "../types/api";

interface HeaderProps {
  onOpenKillSwitch: () => void;
}

export function Header({ onOpenKillSwitch }: HeaderProps) {
  const [istTime, setIstTime] = useState<string>("");
  const [health, setHealth] = useState<SystemHealthResponse | null>(null);
  const [session, setSession] = useState<SessionActiveResponse | null>(null);
  const [risk, setRisk] = useState<RiskSnapshotResponse | null>(null);

  useEffect(() => {
    // Ticking IST clock
    const updateTime = () => {
      const now = new Date();
      setIstTime(
        now.toLocaleTimeString("en-IN", {
          timeZone: "Asia/Kolkata",
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      );
    };
    updateTime();
    const clockTimer = setInterval(updateTime, 1000);

    // Fetch initial status & poll
    const fetchStatus = async () => {
      try {
        const [h, s, r] = await Promise.allSettled([
          api.getHealth(),
          api.getActiveSession(),
          api.getRiskSnapshot(),
        ]);
        if (h.status === "fulfilled") setHealth(h.value);
        if (s.status === "fulfilled") setSession(s.value);
        if (r.status === "fulfilled") setRisk(r.value);
      } catch (err) {
        // ignore background poll errors
      }
    };
    fetchStatus();
    const pollTimer = setInterval(fetchStatus, 3000);

    return () => {
      clearInterval(clockTimer);
      clearInterval(pollTimer);
    };
  }, []);

  const totalMtm = risk ? parseFloat(String(risk.total_mtm)) : null;

  return (
    <header className="h-13 border-b border-[#182133] bg-[#090d14] px-4 flex items-center justify-between sticky top-0 z-40 select-none shadow-sm shadow-black/40">
      {/* Left: Brand, Architecture Mode, and Data Source Badge */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center space-x-2.5">
          <div className="h-7 w-7 rounded bg-gradient-to-br from-cyan-600 to-blue-700 flex items-center justify-center font-bold text-white text-xs shadow-sm shadow-cyan-900/40 border border-cyan-400/30">
            TV
          </div>
          <div className="flex flex-col">
            <div className="flex items-center space-x-1.5 leading-none">
              <span className="font-bold tracking-tight text-slate-100 text-sm">
                MyTradeView
              </span>
              <span className="text-[10px] font-mono text-cyan-400 bg-cyan-950/60 px-1 py-0.2 rounded border border-cyan-800/40">
                PRO
              </span>
            </div>
            <span className="text-[10px] text-slate-400 font-mono tracking-tight leading-tight">
              NIFTY ORB Engine
            </span>
          </div>
        </div>

        <div className="h-4 w-px bg-slate-800" />

        {/* Institutional Paper Mode Pill */}
        <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded badge-paper text-[11px] font-mono font-semibold tracking-wider">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span>PAPER MODE</span>
        </div>

        {/* Data Source Badge */}
        <div
          className={`flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-bold tracking-wider uppercase border ${
            health?.data_source === "LIVE"
              ? "bg-emerald-950/60 border-emerald-500/50 text-emerald-300"
              : health?.data_source === "HISTORICAL"
              ? "bg-blue-950/60 border-blue-500/50 text-blue-300"
              : "bg-amber-950/60 border-amber-500/50 text-amber-300"
          }`}
          title={`Active Market Data Source: ${health?.data_source || 'SYNTHETIC'}`}
        >
          <span>DATA: {health?.data_source || "SYNTHETIC"}</span>
        </div>
      </div>

      {/* Center Telemetry: Live Spot, Session MTM, Date, ORB Range, Clock */}
      <div className="hidden lg:flex items-center space-x-4 text-xs font-mono">
        {/* NIFTY Spot Ticker */}
        <div className="flex items-center space-x-2 bg-[#0d121c] px-2.5 py-1 rounded border border-[#1c2638]">
          <TrendingUp className="w-3.5 h-3.5 text-cyan-400" />
          <span className="text-slate-400">NIFTY 50:</span>
          <span className="font-bold text-slate-100 tabular-nums" title={health?.market_data_feed_connected ? "Last live exchange tick" : "No live feed: price unavailable"}>{health?.market_data_feed_connected && health?.live_last_ltp ? Number(health.live_last_ltp).toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "--"}</span>
        </div>

        {/* Session MTM */}
        <div className="flex items-center space-x-2 bg-[#0d121c] px-3 py-1 rounded border border-[#1c2638]">
          <span className="text-slate-400">SESSION MTM:</span>
          {totalMtm !== null ? (
            <span className={`font-bold tabular-nums ${getPnlColor(totalMtm)}`}>
              {formatINR(totalMtm)}
            </span>
          ) : (
            <span className="text-slate-500">--</span>
          )}
        </div>

        {/* Session Date */}
        <div className="flex items-center space-x-1.5 text-slate-300">
          <span className="text-slate-400">DATE:</span>
          <span className="font-medium text-slate-200 tabular-nums">
            {session?.trading_date || "2026-11-02"}
          </span>
        </div>

        {/* Phase */}
        <div className="flex items-center space-x-1.5 text-slate-300">
          <span className="text-slate-400">PHASE:</span>
          <span className={`font-medium ${session?.phase === "ACTIVE_TRADING" ? "text-emerald-400" : "text-blue-400"}`}>
            {session?.phase || "CLOSED"}
          </span>
        </div>

        {/* ORB Status */}
        <div className="flex items-center space-x-1 text-slate-400">
          <span className="text-slate-400">ORB:</span>
          <span className="text-amber-400 font-medium tabular-nums">
            {session?.opening_range?.is_established && session.opening_range.low && session.opening_range.high
              ? `${Number(session.opening_range.low).toFixed(1)} - ${Number(session.opening_range.high).toFixed(1)}`
              : session?.opening_range?.is_established
              ? "ESTABLISHED"
              : "NOT ESTABLISHED"}
          </span>
        </div>

        {/* IST Clock */}
        <div className="flex items-center space-x-1.5 text-slate-300 bg-[#0d121c] px-2.5 py-1 rounded border border-[#1c2638]">
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <span className="font-bold text-slate-200 tabular-nums">{istTime || "--:--:--"}</span>
          <span className="text-[10px] text-slate-400">IST</span>
        </div>
      </div>

      {/* Right Actions & Health Indicators */}
      <div className="flex items-center space-x-3">
        {/* Health Indicators */}
        <div className="hidden sm:flex items-center space-x-2 text-[11px] font-mono text-slate-400 bg-[#0d121c] px-2.5 py-1 rounded border border-[#1c2638]">
          {/* Paper Broker */}
          <div className="flex items-center space-x-1" title={health?.broker_connected ? "Paper Execution Gateway Connected" : "Paper Broker Disconnected"}>
            <Wifi className={`w-3.5 h-3.5 ${health?.broker_connected ? "text-emerald-400" : "text-amber-400"}`} />
            <span className="text-slate-300">PAPER</span>
          </div>

          <span className="text-slate-700">|</span>

          {/* FYERS Auth Status */}
          <div className="flex items-center space-x-1.5" title={health?.fyers_authenticated ? "FYERS API authenticated (verified by API call)" : `FYERS not authenticated: ${health?.fyers_auth_reason || health?.fyers_status || "unknown"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${health?.fyers_authenticated ? "bg-emerald-400" : "bg-amber-400"}`} />
            <span className={health?.fyers_authenticated ? "text-emerald-300" : "text-amber-400"}>
              {health?.fyers_authenticated ? "FYERS: AUTH" : "FYERS: NO-AUTH"}
            </span>
          </div>

          <span className="text-slate-700">|</span>

          {/* Market Feed Status */}
          <div className="flex items-center space-x-1" title={`WebSocket: ${health?.websocket_state || "NOT_STARTED"} | subscribed: ${health?.live_feed_subscribed ? "yes" : "no"} | fresh tick: ${health?.live_feed_fresh_tick ? "yes" : "no"}`}>
            <Activity className={`w-3.5 h-3.5 ${health?.market_data_feed_connected ? "text-emerald-400" : (health?.is_market_open ? "text-amber-400" : "text-slate-500")}`} />
            <span className="text-slate-400">
              {health?.market_data_feed_connected ? "LIVE FEED" : (health?.websocket_state && health.websocket_state !== "NOT_STARTED" ? `FEED: ${health.websocket_state.replace(/_/g, " ")}` : (health?.is_market_open ? "FEED OFF" : "MKT CLOSED"))}
            </span>
          </div>

          <span className="text-slate-700">|</span>

          {/* Reconciliation Sync */}
          <div className="flex items-center space-x-1" title={health?.is_reconciled ? "Internal OMS and Broker State Synchronized" : "State Desynchronized / Divergent"}>
            <CheckCircle2 className={`w-3.5 h-3.5 ${health?.is_reconciled ? "text-emerald-400" : "text-rose-400"}`} />
            <span className={health?.is_reconciled ? "text-slate-300" : "text-rose-400 font-bold"}>
              {health?.is_reconciled ? "SYNC" : "DIVERGENT"}
            </span>
          </div>
        </div>

        {/* Emergency Kill-Switch CTA */}
        <button
          onClick={onOpenKillSwitch}
          className="flex items-center space-x-1.5 px-3 py-1 bg-rose-950/80 hover:bg-rose-900 text-rose-200 font-mono text-xs font-bold rounded border border-rose-800/80 shadow-sm transition-all hover:border-rose-500 active:translate-y-px"
          title="Emergency Liquidation & Lockout"
        >
          <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
          <span>KILL-SWITCH</span>
        </button>
      </div>
    </header>
  );
}
