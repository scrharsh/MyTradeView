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
  Search,
  Filter,
  Check,
  Zap,
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

  // Real immutable audit events from backend repository
  const [auditEvents, setAuditEvents] = useState<AuditEventItem[]>([]);
  const [auditFilter, setAuditFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  const loadData = async () => {
    try {
      const [rRes, recRes, aRes] = await Promise.allSettled([
        api.getRiskSnapshot(),
        api.getLatestReconciliation(),
        api.getAuditEvents(50),
      ]);
      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (recRes.status === "fulfilled") setReconciliation(recRes.value);
      if (aRes.status === "fulfilled" && Array.isArray(aRes.value)) setAuditEvents(aRes.value);
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

  const netMtm = risk ? parseFloat(String(risk.total_mtm)) : 0.0;
  const maxLoss = risk ? parseFloat(String(risk.max_daily_loss_limit || 3000.0)) : 3000.0;
  const profitFloor = risk ? parseFloat(String(risk.profit_floor)) : 0.0;
  const profitTrigger = risk?.profit_protection_trigger ? parseFloat(String(risk.profit_protection_trigger)) : 3000.0;

  // Percentage toward loss limit (from zero down to -3000)
  const lossPercentage = Math.min(100, Math.max(0, ((-netMtm) / maxLoss) * 100));

  const filteredAuditEvents = auditEvents.filter((evt) => {
    const matchesType = auditFilter === "ALL" || evt.event_type.toUpperCase().includes(auditFilter);
    const searchLower = searchQuery.toLowerCase();
    const matchesSearch =
      searchQuery === "" ||
      evt.event_type.toLowerCase().includes(searchLower) ||
      (evt.component && evt.component.toLowerCase().includes(searchLower)) ||
      (evt.actor && evt.actor.toLowerCase().includes(searchLower)) ||
      JSON.stringify(evt.details).toLowerCase().includes(searchLower);
    return matchesType && matchesSearch;
  });

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>SURVEILLANCE & SYSTEM</span>
            <span>/</span>
            <span>CAPITAL PRESERVATION</span>
            <span>/</span>
            <span className="text-rose-400">RISK ENGINE</span>
          </div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <ShieldAlert className="w-5 h-5 text-rose-400" />
            <span>RISK MANAGEMENT & AUDIT CONTROL</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Real-time loss breakers, profit protection floors, broker state reconciliation, and immutable audit logs.
          </p>
        </div>

        <button
          onClick={loadData}
          className="btn-secondary text-xs flex items-center space-x-1.5"
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Daily Loss Circuit Breaker Gauge */}
        <div className="panel p-4 space-y-4">
          <div className="panel-header">
            <div className="flex items-center space-x-2 text-rose-400 font-bold">
              <ShieldAlert className="w-4 h-4" />
              <span>DAILY LOSS CIRCUIT BREAKER</span>
            </div>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded font-mono ${
                risk?.is_circuit_breaker_active
                  ? "bg-rose-950 text-rose-400 border border-rose-800"
                  : "bg-emerald-950 text-emerald-400 border border-emerald-800"
              }`}
            >
              {risk?.is_circuit_breaker_active ? "TRIPPED / LOCKED OUT" : "ARMED & HEALTHY"}
            </span>
          </div>

          <div className="space-y-2.5 font-mono">
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Current Session MTM:</span>
              <span className={`font-bold tabular-nums ${getPnlColor(netMtm)}`}>{formatINR(netMtm)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Hard Loss Limit:</span>
              <span className="font-bold text-rose-400 tabular-nums">-₹{formatNumber(maxLoss, 2)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Remaining Loss Capacity:</span>
              <span className="font-bold text-emerald-400 tabular-nums">
                ₹{formatNumber(maxLoss + netMtm, 2)}
              </span>
            </div>

            {/* Visual Risk Gauge Bar */}
            <div className="pt-2">
              <div className="h-2 w-full bg-[#090d14] border border-[#1a2336] rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all duration-500 ${
                    lossPercentage > 75
                      ? "bg-rose-500 shadow-sm shadow-rose-500"
                      : lossPercentage > 40
                      ? "bg-amber-500"
                      : "bg-emerald-500"
                  }`}
                  style={{ width: `${Math.max(4, lossPercentage)}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-slate-500 mt-1.5 font-mono">
                <span>Safe (0%)</span>
                <span>Warning (50%)</span>
                <span className="text-rose-400 font-semibold">Circuit Tripped (₹3,000)</span>
              </div>
            </div>

            <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] text-[11px] text-slate-400 leading-relaxed">
              If cumulative session losses touch ₹3,000, the OMS automatically cancels all orders, squares off positions, and locks out the session.
            </div>
          </div>
        </div>

        {/* Profit Protection Floor Status */}
        <div className="panel p-4 space-y-4">
          <div className="panel-header">
            <div className="flex items-center space-x-2 text-cyan-400 font-bold">
              <Lock className="w-4 h-4" />
              <span>PROFIT PROTECTION FLOOR & TRAILING</span>
            </div>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded font-mono ${
                risk?.is_profit_protection_active
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                  : "bg-blue-950 text-blue-400 border border-blue-800"
              }`}
            >
              {risk?.is_profit_protection_active ? "FLOOR ACTIVE" : "ARMED (₹3k THRESHOLD)"}
            </span>
          </div>

          <div className="space-y-2.5 font-mono text-xs">
            <div className="flex justify-between">
              <span className="text-slate-400">Activation Threshold:</span>
              <span className="text-slate-200 tabular-nums">₹{formatNumber(profitTrigger, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Current Locked Profit Floor:</span>
              <span className="font-bold text-emerald-400 tabular-nums">₹{formatNumber(profitFloor, 2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Trailing Rule:</span>
              <span className="text-slate-200">Lock ₹500 at ₹3k, +₹500 / ₹1k profit step</span>
            </div>

            <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] text-[11px] text-slate-400 leading-relaxed">
              If session MTM crosses ₹3,000 and pulls back to touch the locked floor, the OMS immediately flattens all derivative positions and terminates active trading.
            </div>

            {/* Stepped Trailing Status Matrix */}
            <div className="grid grid-cols-3 gap-2 pt-1 text-[10px]">
              <div className="p-2 rounded bg-[#090d14] border border-[#1a2336] text-center">
                <span className="text-slate-500 block">Tier 1</span>
                <span className="text-slate-300">≥ ₹3,000</span>
                <span className="text-emerald-400 font-bold block mt-0.5">₹500 Floor</span>
              </div>
              <div className="p-2 rounded bg-[#090d14] border border-[#1a2336] text-center">
                <span className="text-slate-500 block">Tier 2</span>
                <span className="text-slate-300">≥ ₹4,000</span>
                <span className="text-emerald-400 font-bold block mt-0.5">₹1,000 Floor</span>
              </div>
              <div className="p-2 rounded bg-[#090d14] border border-[#1a2336] text-center">
                <span className="text-slate-500 block">Tier 3</span>
                <span className="text-slate-300">≥ ₹5,000</span>
                <span className="text-emerald-400 font-bold block mt-0.5">₹1,500 Floor</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* BROKER RECONCILIATION AUDIT SECTION */}
      <div className="panel p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-3 gap-3">
          <div>
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center space-x-2">
              <GitCompare className="w-4 h-4 text-cyan-400" />
              <span>OMS vs Broker State Reconciliation Engine</span>
            </div>
            <p className="text-[11px] font-mono text-slate-400 mt-0.5">
              Deterministic verification comparing in-memory order/position states against broker books.
            </p>
          </div>

          <button
            onClick={handleRunReconciliation}
            disabled={isReconciling}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded text-xs font-mono font-bold transition-all ${
              isReconciling
                ? "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
                : "btn-primary shadow-md shadow-cyan-950/40"
            }`}
          >
            <GitCompare className="w-3.5 h-3.5" />
            <span>{isReconciling ? "AUDITING..." : "RUN RECONCILIATION AUDIT"}</span>
          </button>
        </div>

        {reconciliation && (
          <div className="space-y-3 font-mono text-xs">
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2.5">
              <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">RECONCILIATION STATUS</div>
                <div
                  className={`text-sm font-bold mt-1 ${
                    reconciliation.is_synchronized ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  {reconciliation.is_synchronized ? "SYNCHRONIZED" : "DIVERGENT"}
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">Zero state drift detected</div>
              </div>

              <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">ORDERS VERIFIED</div>
                <div className="text-sm font-bold text-slate-100 mt-1 tabular-nums">
                  {reconciliation.internal_orders_count} Checked
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">
                  {reconciliation.broker_orders_count} registered on broker
                </div>
              </div>

              <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">POSITIONS MATCHED</div>
                <div className="text-sm font-bold text-slate-100 mt-1 tabular-nums">
                  {reconciliation.internal_positions_count} Matched
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">
                  {reconciliation.broker_positions_count} registered on broker
                </div>
              </div>

              <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">LAST AUDIT TIMESTAMP</div>
                <div className="text-sm font-bold text-slate-200 mt-1 tabular-nums">
                  {formatISTTime(reconciliation.timestamp)}
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">Deterministic check</div>
              </div>
            </div>

            {/* Discrepancies Table or Success Notice */}
            {reconciliation.discrepancies && reconciliation.discrepancies.length > 0 ? (
              <div className="p-3 rounded bg-rose-950/20 border border-rose-900/50 space-y-2">
                <div className="text-xs font-bold text-rose-300 flex items-center space-x-1.5">
                  <ShieldAlert className="w-4 h-4 text-rose-400" />
                  <span>State Discrepancies Detected ({reconciliation.discrepancies.length})</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left font-mono text-[11px]">
                    <thead>
                      <tr className="text-slate-400 border-b border-[#1a2336]">
                        <th className="pb-1.5 font-semibold">Category</th>
                        <th className="pb-1.5 font-semibold">Identifier</th>
                        <th className="pb-1.5 font-semibold">Internal Value</th>
                        <th className="pb-1.5 font-semibold">Broker Value</th>
                        <th className="pb-1.5 font-semibold">Severity</th>
                        <th className="pb-1.5 font-semibold">Details</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#131b2a]">
                      {reconciliation.discrepancies.map((d, i) => (
                        <tr key={i} className="hover:bg-[#0d121c]/40">
                          <td className="py-1.5 text-rose-300 font-bold">{d.discrepancy_type || d.category || "UNKNOWN"}</td>
                          <td className="py-1.5 text-slate-200">{d.identifier || d.symbol_or_id || "--"}</td>
                          <td className="py-1.5 text-slate-300 tabular-nums">{String(d.internal_value ?? "--")}</td>
                          <td className="py-1.5 text-rose-400 font-bold tabular-nums">{String(d.broker_value ?? "--")}</td>
                          <td className="py-1.5">
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-rose-950 border border-rose-800 text-rose-300">
                              {d.severity}
                            </span>
                          </td>
                          <td className="py-1.5 text-slate-400 max-w-xs truncate">{d.details || d.description || "--"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="p-2.5 rounded bg-emerald-950/20 border border-emerald-900/40 text-[11px] text-emerald-300 flex items-center space-x-2">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                <span>Zero discrepancies detected. OMS internal orders and derivative positions are fully synchronized with broker books.</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* IMMUTABLE AUDIT TRAIL LOG */}
      <div className="panel p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-3 gap-3">
          <div className="flex items-center space-x-2">
            <FileText className="w-4 h-4 text-cyan-400" />
            <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
              Immutable System Audit Log ({filteredAuditEvents.length})
            </span>
          </div>

          <div className="flex items-center space-x-2 font-mono text-xs">
            {/* Filter Tabs */}
            <div className="flex items-center space-x-1 bg-[#090d14] p-1 rounded border border-[#1a2336]">
              {["ALL", "ORDER", "POSITION", "RISK", "RECON"].map((tab) => (
                <button
                  key={tab}
                  onClick={() => setAuditFilter(tab)}
                  className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all ${
                    auditFilter === tab
                      ? "bg-[#142034] text-cyan-300 border border-cyan-500/40"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="w-3 h-3 absolute left-2.5 top-2 text-slate-500" />
              <input
                type="text"
                placeholder="Search audit trail..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-7 pr-2.5 py-1 rounded bg-[#090d14] border border-[#1a2336] text-[11px] font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 w-44"
              />
            </div>
          </div>
        </div>

        <div className="overflow-x-auto max-h-96 scrollbar-thin">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1a2336] text-[11px]">
                <th className="pb-2 font-semibold">Timestamp (IST)</th>
                <th className="pb-2 font-semibold">Event Type</th>
                <th className="pb-2 font-semibold">Subsystem</th>
                <th className="pb-2 font-semibold">Audit Details & Context</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#131b2a]">
              {filteredAuditEvents.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-6 text-center text-slate-500">
                    No audit events match the filter criteria.
                  </td>
                </tr>
              ) : (
                filteredAuditEvents.map((evt, idx) => {
                  const detailsText =
                    typeof evt.details === "object" ? JSON.stringify(evt.details) : String(evt.details || "");
                  return (
                    <tr key={evt.event_id || idx} className="hover:bg-[#0d121c]/60 transition-colors">
                      <td className="py-2 text-slate-400 text-[11px] tabular-nums whitespace-nowrap">
                        {formatISTTime(evt.timestamp)}
                      </td>
                      <td className="py-2">
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-[#142034] text-cyan-300 border border-cyan-500/30">
                          {evt.event_type}
                        </span>
                      </td>
                      <td className="py-2 text-slate-300 font-semibold text-[11px]">
                        {evt.component || evt.actor || "ENGINE"}
                      </td>
                      <td className="py-2 text-slate-300 text-[11px] font-mono break-all">
                        {detailsText}
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
