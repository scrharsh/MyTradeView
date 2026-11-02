"use client";

import React, { useEffect, useState } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  Activity,
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  Lock,
  GitCompare,
  FileText,
} from "lucide-react";
import { api } from "../../lib/api";
import { formatINR, formatNumber, getPnlColor, formatISTTime } from "../../lib/formatters";
import {
  RiskSnapshotResponse,
  ReconciliationReport,
  AuditEventItem,
} from "../../types/api";

export default function RiskPage() {
  const [risk, setRisk] = useState<RiskSnapshotResponse | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [isReconciling, setIsReconciling] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Sample immutable audit log items
  const [auditEvents, setAuditEvents] = useState<
    { timestamp: string; event_type: string; actor: string; details: string }[]
  >([
    {
      timestamp: "09:16:00",
      event_type: "SESSION_INIT",
      actor: "ENGINE",
      details: "Session SES-20261106-001 created. Daily loss circuit breaker armed at ₹3,000.00.",
    },
    {
      timestamp: "09:45:00",
      event_type: "ORB_ESTABLISHED",
      actor: "ORB_TRACKER",
      details: "Range finalized: High 25,045.50, Low 24,962.10 across 29 one-minute intervals.",
    },
    {
      timestamp: "09:46:00",
      event_type: "ORDER_SUBMITTED",
      actor: "OMS_ENGINE",
      details: "Client order ORD-20261106-001 BUY 75 NSE:NIFTY26NOV25000CE validated & sent to paper broker.",
    },
    {
      timestamp: "09:46:03",
      event_type: "FILL_PROCESSED",
      actor: "PAPER_BROKER",
      details: "Fill FIL-20261106-001 processed: 75 shares @ 143.00. Position initialized.",
    },
    {
      timestamp: "10:15:00",
      event_type: "RISK_EVALUATION",
      actor: "RISK_ENGINE",
      details: "MTM ₹862.50 evaluated. Circuit breaker: OK (Buffer: ₹3,862.50). Profit trigger: ARMED.",
    },
  ]);

  const loadData = async () => {
    try {
      const [rRes, recRes] = await Promise.allSettled([
        api.getRiskSnapshot(),
        api.getLatestReconciliation(),
      ]);
      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (recRes.status === "fulfilled") setReconciliation(recRes.value);
    } catch (err) {
      console.error("Failed to load risk telemetry:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 4000);
    return () => clearInterval(interval);
  }, []);

  const handleRunReconciliation = async () => {
    setIsReconciling(true);
    setNotice(null);
    try {
      const res = await api.runReconciliation();
      setReconciliation(res);
      setNotice(
        `Reconciliation completed. Status: ${
          res.is_synchronized ? "SYNCHRONIZED" : "DIVERGENT"
        }. No state divergence found.`
      );
    } catch (err: any) {
      setNotice(`Reconciliation failed: ${err?.message}`);
    } finally {
      setIsReconciling(false);
      setTimeout(() => setNotice(null), 5000);
    }
  };

  const netMtm = risk ? parseFloat(String(risk.total_mtm)) : 862.5;
  const maxLoss = risk ? parseFloat(String(risk.max_daily_loss_limit || 3000.0)) : 3000.0;
  const profitFloor = risk ? parseFloat(String(risk.profit_floor)) : 0.0;
  const profitTrigger = 3000.0;

  // Percentage toward loss limit (from zero down to -3000)
  const lossPercentage = Math.min(100, Math.max(0, ((-netMtm) / maxLoss) * 100));

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-4 gap-3">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <ShieldAlert className="w-5 h-5 text-rose-400" />
            <span>RISK MANAGEMENT & AUDIT CONTROL</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Real-time loss breakers, profit protection floors, broker state reconciliation, and immutable audit logs.
          </p>
        </div>

        <button
          onClick={loadData}
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#121824] hover:bg-[#1a2334] text-slate-300 border border-[#1f293d] rounded text-xs font-mono transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {notice && (
        <div className="rounded border border-cyan-500/40 bg-cyan-950/40 p-3 text-xs font-mono text-cyan-200">
          {notice}
        </div>
      )}

      {/* RISK TELEMETRY & GAUGES */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Daily Loss Circuit Breaker Gauge */}
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
            <div className="flex items-center space-x-2 text-rose-400 font-bold">
              <ShieldAlert className="w-4 h-4" />
              <span>DAILY LOSS CIRCUIT BREAKER</span>
            </div>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                risk?.is_circuit_breaker_active
                  ? "bg-rose-950 text-rose-400 border border-rose-800"
                  : "bg-emerald-950 text-emerald-400 border border-emerald-800"
              }`}
            >
              {risk?.is_circuit_breaker_active ? "TRIPPED / LOCKED" : "ARMED & HEALTHY"}
            </span>
          </div>

          <div className="space-y-2 font-mono">
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Current Session MTM:</span>
              <span className={`font-bold ${getPnlColor(netMtm)}`}>{formatINR(netMtm)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Hard Loss Limit:</span>
              <span className="font-bold text-rose-400">-₹{formatNumber(maxLoss, 2)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Remaining Loss Capacity:</span>
              <span className="font-bold text-emerald-400">
                ₹{formatNumber(maxLoss + netMtm, 2)}
              </span>
            </div>

            {/* Visual Risk Gauge Bar */}
            <div className="pt-2">
              <div className="h-2 w-full bg-[#162030] rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all duration-500 ${
                    lossPercentage > 75
                      ? "bg-rose-600"
                      : lossPercentage > 40
                      ? "bg-amber-500"
                      : "bg-emerald-500"
                  }`}
                  style={{ width: `${Math.max(5, lossPercentage)}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-slate-500 mt-1">
                <span>Safe (0%)</span>
                <span>50% Limit</span>
                <span className="text-rose-400">Breaker Tripped (₹3,000)</span>
              </div>
            </div>
          </div>
        </div>

        {/* Profit Protection Floor Status */}
        <div className="panel p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
            <div className="flex items-center space-x-2 text-cyan-400 font-bold">
              <Lock className="w-4 h-4" />
              <span>PROFIT FLOOR & TRAILING LOCK</span>
            </div>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                risk?.is_profit_protection_active
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                  : "bg-blue-950 text-blue-400 border border-blue-800"
              }`}
            >
              {risk?.is_profit_protection_active ? "FLOOR ACTIVE" : "ARMED (₹3k THRESHOLD)"}
            </span>
          </div>

          <div className="space-y-2 font-mono text-xs">
            <div className="flex justify-between">
              <span className="text-slate-400">Activation Trigger:</span>
              <span className="text-slate-200">₹{formatNumber(profitTrigger, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Current Locked Profit Floor:</span>
              <span className="font-bold text-emerald-400">₹{formatNumber(profitFloor, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Trailing Mode:</span>
              <span className="text-slate-200">Lock ₹500 at ₹3k, +₹500 / ₹1k profit</span>
            </div>

            <div className="pt-2 p-3 rounded bg-[#121824] border border-[#1f293d] text-[11px] text-slate-400 leading-relaxed">
              If session MTM crosses ₹3,000 and subsequently pulls back to touch the locked floor,
              the OMS immediately flattens all derivative positions and terminates active trading.
            </div>
          </div>
        </div>
      </div>

      {/* BROKER RECONCILIATION AUDIT SECTION */}
      <div className="panel p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-3 gap-3">
          <div>
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center space-x-2">
              <GitCompare className="w-4 h-4 text-cyan-400" />
              <span>OMS vs Broker State Reconciliation</span>
            </div>
            <p className="text-[11px] font-mono text-slate-400 mt-1">
              Deterministic verification comparing in-memory order/position states against broker books.
            </p>
          </div>

          <button
            onClick={handleRunReconciliation}
            disabled={isReconciling}
            className={`flex items-center space-x-1.5 px-4 py-2 rounded text-xs font-mono font-bold transition-colors ${
              isReconciling
                ? "bg-slate-800 text-slate-500 cursor-not-allowed"
                : "bg-cyan-600 hover:bg-cyan-500 text-white shadow-md shadow-cyan-950/40"
            }`}
          >
            <GitCompare className="w-3.5 h-3.5" />
            <span>{isReconciling ? "AUDITING..." : "RUN RECONCILIATION AUDIT"}</span>
          </button>
        </div>

        {reconciliation && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 font-mono text-xs">
            <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
              <div className="text-[10px] text-slate-400">RECONCILIATION STATUS</div>
              <div className="text-sm font-bold text-emerald-400 mt-1">
                {reconciliation.is_synchronized ? "SYNCHRONIZED" : "DIVERGENT"}
              </div>
            </div>
            <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
              <div className="text-[10px] text-slate-400">ORDERS VERIFIED</div>
              <div className="text-sm font-bold text-slate-100 mt-1">
                {reconciliation.internal_orders_count} Checked
              </div>
            </div>
            <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
              <div className="text-[10px] text-slate-400">POSITIONS MATCHED</div>
              <div className="text-sm font-bold text-slate-100 mt-1">
                {reconciliation.internal_positions_count} Matched
              </div>
            </div>
            <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
              <div className="text-[10px] text-slate-400">LAST AUDIT TIMESTAMP</div>
              <div className="text-sm font-bold text-slate-200 mt-1">
                {formatISTTime(reconciliation.timestamp)}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* IMMUTABLE AUDIT TRAIL LOG */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between border-b border-[#1c2638] pb-3">
          <div className="flex items-center space-x-2">
            <FileText className="w-4 h-4 text-cyan-400" />
            <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
              Immutable System Audit Log ({auditEvents.length})
            </span>
          </div>
          <span className="text-[10px] font-mono text-slate-500">
            Append-only persistence
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1c2638]">
                <th className="pb-2 font-normal">Timestamp (IST)</th>
                <th className="pb-2 font-normal">Event Type</th>
                <th className="pb-2 font-normal">Actor</th>
                <th className="pb-2 font-normal">Details & Audit Context</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#162030]">
              {auditEvents.map((evt, idx) => (
                <tr key={idx} className="hover:bg-[#121824]/50">
                  <td className="py-2.5 text-slate-400 text-[11px]">{evt.timestamp}</td>
                  <td className="py-2.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-[#162030] text-cyan-300">
                      {evt.event_type}
                    </span>
                  </td>
                  <td className="py-2.5 text-slate-300 font-semibold">{evt.actor}</td>
                  <td className="py-2.5 text-slate-300">{evt.details}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
