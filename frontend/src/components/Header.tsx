"use client";

import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  Activity,
  ShieldAlert,
  Wifi,
  Database,
  Clock,
  CheckCircle2,
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
    <header className="h-14 border-b border-[#1c2638] bg-[#0c1017] px-4 flex items-center justify-between sticky top-0 z-40 select-none">
      {/* Brand & Mode */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center space-x-2">
          <div className="h-7 w-7 rounded bg-blue-600 flex items-center justify-center font-bold text-white text-sm shadow-md shadow-blue-500/20">
            TV
          </div>
          <span className="font-bold tracking-tight text-slate-100 text-base">
            MyTradeView
          </span>
          <span className="text-xs text-slate-500 font-mono hidden sm:inline">
            v1.0-NIFTY
          </span>
        </div>

        {/* Institutional PAPER mode pill */}
        <div className="flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full badge-paper text-xs font-mono font-semibold tracking-wider">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>PAPER MODE</span>
        </div>
      </div>

      {/* Center Telemetry: MTM, Session Phase, Clock */}
      <div className="hidden md:flex items-center space-x-6 text-xs font-mono">
        {/* Live MTM */}
        <div className="flex items-center space-x-2 bg-[#121824] px-3 py-1 rounded border border-[#1f293d]">
          <span className="text-slate-400">SESSION MTM:</span>
          {totalMtm !== null ? (
            <span className={`font-bold ${getPnlColor(totalMtm)}`}>
              {formatINR(totalMtm)}
            </span>
          ) : (
            <span className="text-slate-500">--</span>
          )}
        </div>

        {/* Phase */}
        <div className="flex items-center space-x-1.5 text-slate-300">
          <span className="text-slate-500">PHASE:</span>
          <span className="font-medium text-blue-400">
            {session?.phase || "ACTIVE_TRADING"}
          </span>
        </div>

        {/* Opening Range status */}
        <div className="flex items-center space-x-1 text-slate-400">
          <span className="text-slate-500">ORB:</span>
          <span className="text-amber-400 font-medium">
            {session?.opening_range?.is_established && session.opening_range.low && session.opening_range.high
              ? `${Number(session.opening_range.low).toFixed(1)} - ${Number(session.opening_range.high).toFixed(1)}`
              : session?.opening_range?.is_established
              ? "ESTABLISHED"
              : "NOT ESTABLISHED"}
          </span>
        </div>

        {/* Clock */}
        <div className="flex items-center space-x-1 text-slate-300 bg-[#121824] px-2.5 py-1 rounded border border-[#1f293d]">
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <span className="font-bold text-slate-200">{istTime || "--:--:--"}</span>
          <span className="text-[10px] text-slate-500">IST</span>
        </div>
      </div>

      {/* Right Actions & Health Indicators */}
      <div className="flex items-center space-x-3">
        {/* Health Indicators */}
        <div className="hidden lg:flex items-center space-x-2 text-[11px] font-mono text-slate-400 bg-[#121824] px-2.5 py-1 rounded border border-[#1f293d]">
          <div className="flex items-center space-x-1" title={health?.broker_connected ? "Broker Connected" : "Broker Disconnected"}>
            <Wifi className={`w-3.5 h-3.5 ${health?.broker_connected ? "text-emerald-400" : "text-amber-400"}`} />
            <span className="text-slate-300">BROKER</span>
          </div>
          <span className="text-slate-600">|</span>
          <div className="flex items-center space-x-1" title={health?.database_connected ? "Database Connected" : "Database Offline"}>
            <Database className={`w-3.5 h-3.5 ${health?.database_connected ? "text-emerald-400" : "text-rose-400"}`} />
            <span className="text-slate-300">DB</span>
          </div>
          <span className="text-slate-600">|</span>
          <div className="flex items-center space-x-1" title={health?.is_reconciled ? "State Synchronized" : "Reconciliation Pending"}>
            <CheckCircle2 className={`w-3.5 h-3.5 ${health?.is_reconciled ? "text-emerald-400" : "text-amber-400"}`} />
            <span className="text-slate-300">SYNC</span>
          </div>
        </div>

        {/* Emergency Kill-Switch CTA */}
        <button
          onClick={onOpenKillSwitch}
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-rose-600/90 hover:bg-rose-600 text-white font-mono text-xs font-semibold rounded border border-rose-500 shadow-sm shadow-rose-900/30 transition-colors"
          title="Emergency Liquidation & Lockout"
        >
          <ShieldAlert className="w-3.5 h-3.5 text-rose-200" />
          <span>KILL-SWITCH</span>
        </button>
      </div>
    </header>
  );
}

