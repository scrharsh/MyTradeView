"use client";

import React, { useEffect, useState } from "react";
import {
  ListOrdered,
  Search,
  Filter,
  CheckCircle2,
  XCircle,
  Clock,
  Eye,
  X,
  RefreshCw,
  AlertOctagon,
  ArrowRight,
  ShieldCheck,
} from "lucide-react";
import { api } from "../../lib/api";
import {
  formatINR,
  formatNumber,
  getPnlColor,
  formatISTTime,
  formatISTDateTime,
} from "../../lib/formatters";
import { OrderResponseItem, PositionResponseItem } from "../../types/api";

export default function OrdersPage() {
  const [orders, setOrders] = useState<OrderResponseItem[]>([]);
  const [positions, setPositions] = useState<PositionResponseItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedOrder, setSelectedOrder] = useState<OrderResponseItem | null>(null);
  const [squaringOff, setSquaringOff] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = async () => {
    try {
      const [oRes, pRes] = await Promise.allSettled([api.getOrders(), api.getPositions()]);
      if (oRes.status === "fulfilled") {
        setOrders(Array.isArray(oRes.value) ? oRes.value : []);
      }
      if (pRes.status === "fulfilled") {
        setPositions(Array.isArray(pRes.value) ? pRes.value : []);
      }
    } catch (err) {
      console.error("Failed to load orders/positions:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 4000);
    return () => clearInterval(interval);
  }, []);

  const handleSquareOff = async (symbol: string) => {
    if (!confirm(`Confirm immediate square-off for ${symbol}?`)) return;
    setSquaringOff(symbol);
    try {
      const res = await api.squareOffPosition(symbol);
      setNotice(`Square-off completed for ${symbol}. Position status: ${res.is_open ? "OPEN" : "CLOSED"}`);
      await loadData();
    } catch (err: any) {
      setNotice(`Failed to square off: ${err?.message}`);
    } finally {
      setSquaringOff(null);
      setTimeout(() => setNotice(null), 4000);
    }
  };

  const filteredOrders = orders.filter((o) => {
    const matchesStatus = statusFilter === "ALL" || o.status === statusFilter;
    const matchesQuery =
      searchQuery === "" ||
      o.client_order_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (o.broker_order_id && o.broker_order_id.toLowerCase().includes(searchQuery.toLowerCase())) ||
      o.symbol.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesStatus && matchesQuery;
  });

  const orderCounts = {
    ALL: orders.length,
    FILLED: orders.filter((o) => o.status === "FILLED").length,
    OPEN: orders.filter((o) => o.status === "OPEN" || o.status === "PENDING").length,
    CANCELLED: orders.filter((o) => o.status === "CANCELLED").length,
    REJECTED: orders.filter((o) => o.status === "REJECTED").length,
  };

  return (
    <div className="space-y-5">
      {/* Page Title & Controls */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>TRADING & EXECUTION</span>
            <span>/</span>
            <span>ORDER MANAGEMENT SYSTEM</span>
            <span>/</span>
            <span className="text-cyan-400">EXECUTION BLOTTER</span>
          </div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <ListOrdered className="w-5 h-5 text-cyan-400" />
            <span>ORDERS & POSITIONS AUDIT BLOTTER</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Complete order lifecycle history, partial fills, and real-time position liquidation controls.
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

      {/* POSITIONS SECTION */}
      <div className="panel p-4 space-y-3">
        <div className="panel-header">
          <div className="flex items-center space-x-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="uppercase tracking-wider">
              Open Derivative Portfolio ({positions.length})
            </span>
          </div>
          <span className="text-[10px] font-mono text-slate-500">
            Execution Mode: Strictly PAPER
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1a2336] text-[11px]">
                <th className="pb-2 font-semibold">Position ID</th>
                <th className="pb-2 font-semibold">Contract</th>
                <th className="pb-2 font-semibold">Side</th>
                <th className="pb-2 font-semibold text-right">Net Qty</th>
                <th className="pb-2 font-semibold text-right">Avg Entry</th>
                <th className="pb-2 font-semibold text-right">LTP</th>
                <th className="pb-2 font-semibold text-right">Realized P&L</th>
                <th className="pb-2 font-semibold text-right">Unrealized P&L</th>
                <th className="pb-2 font-semibold text-right">Total Net P&L</th>
                <th className="pb-2 font-semibold text-center">Liquidation</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#131b2a]">
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-slate-500">
                    No open positions at this time.
                  </td>
                </tr>
              ) : (
                positions.map((p) => {
                  const isLong = p.net_qty > 0;
                  const total = Number(p.total_pnl);
                  return (
                    <tr key={p.position_id} className="hover:bg-[#0d121c]/60 transition-colors">
                      <td className="py-2.5 font-bold text-slate-300">{p.position_id}</td>
                      <td className="py-2.5 font-semibold text-slate-100">{p.symbol}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            isLong
                              ? "text-emerald-400 bg-emerald-950/70 border border-emerald-800"
                              : "text-rose-400 bg-rose-950/70 border border-rose-800"
                          }`}
                        >
                          {isLong ? "LONG" : "SHORT"}
                        </span>
                      </td>
                      <td className="py-2.5 text-right font-medium tabular-nums">{Math.abs(p.net_qty)}</td>
                      <td className="py-2.5 text-right text-slate-300 tabular-nums">
                        ₹{formatNumber(p.avg_buy_price, 2)}
                      </td>
                      <td className="py-2.5 text-right font-bold text-slate-100 tabular-nums">
                        ₹{formatNumber(p.current_ltp, 2)}
                      </td>
                      <td className="py-2.5 text-right text-slate-400 tabular-nums">{formatINR(p.realized_pnl)}</td>
                      <td className={`py-2.5 text-right tabular-nums ${getPnlColor(p.unrealized_pnl)}`}>
                        {formatINR(p.unrealized_pnl)}
                      </td>
                      <td className={`py-2.5 text-right font-bold tabular-nums ${getPnlColor(total)}`}>
                        {formatINR(total)}
                      </td>
                      <td className="py-2.5 text-center">
                        <button
                          onClick={() => handleSquareOff(p.symbol)}
                          disabled={squaringOff === p.symbol}
                          className="px-2.5 py-1 bg-rose-950/70 hover:bg-rose-900 text-rose-300 border border-rose-800/80 rounded text-[10px] font-bold transition-all"
                        >
                          {squaringOff === p.symbol ? "Closing..." : "Square-Off"}
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

      {/* ORDERS SECTION WITH FILTERING & SEARCH */}
      <div className="panel p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-3 gap-3">
          <div className="flex items-center space-x-2">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
            <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
              Order Lifecycle Management ({filteredOrders.length})
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
            {/* Status Tabs */}
            <div className="flex items-center space-x-1 bg-[#090d14] p-1 rounded border border-[#1a2336]">
              {(["ALL", "FILLED", "OPEN", "CANCELLED", "REJECTED"] as const).map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all flex items-center space-x-1 ${
                    statusFilter === st
                      ? "bg-[#142034] text-cyan-300 border border-cyan-500/40"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <span>{st}</span>
                  <span className="text-[9px] text-slate-500 tabular-nums">({orderCounts[st] || 0})</span>
                </button>
              ))}
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-slate-500" />
              <input
                type="text"
                placeholder="Search ID / Symbol..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1 rounded bg-[#090d14] border border-[#1a2336] text-xs font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500 w-48"
              />
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1a2336] text-[11px]">
                <th className="pb-2 font-semibold">Client Order ID</th>
                <th className="pb-2 font-semibold">Broker Order ID</th>
                <th className="pb-2 font-semibold">Instrument</th>
                <th className="pb-2 font-semibold">Side</th>
                <th className="pb-2 font-semibold text-right">Qty</th>
                <th className="pb-2 font-semibold text-right">Filled</th>
                <th className="pb-2 font-semibold">Type</th>
                <th className="pb-2 font-semibold text-center">Status</th>
                <th className="pb-2 font-semibold text-right">Time (IST)</th>
                <th className="pb-2 font-semibold text-center">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#131b2a]">
              {filteredOrders.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-slate-500">
                    No orders matching selected criteria.
                  </td>
                </tr>
              ) : (
                filteredOrders.map((ord) => {
                  const isBuy = ord.side === "BUY";
                  return (
                    <tr key={ord.client_order_id} className="hover:bg-[#0d121c]/60 transition-colors">
                      <td className="py-2.5 font-bold text-slate-300">{ord.client_order_id}</td>
                      <td className="py-2.5 text-slate-400 text-[11px] font-mono">
                        {ord.broker_order_id || "—"}
                      </td>
                      <td className="py-2.5 font-semibold text-slate-100">{ord.symbol}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            isBuy
                              ? "text-emerald-400 bg-emerald-950/70 border border-emerald-800"
                              : "text-rose-400 bg-rose-950/70 border border-rose-800"
                          }`}
                        >
                          {ord.side}
                        </span>
                      </td>
                      <td className="py-2.5 text-right font-medium tabular-nums">{ord.qty}</td>
                      <td className="py-2.5 text-right text-slate-300 tabular-nums">{ord.filled_qty}</td>
                      <td className="py-2.5 text-slate-400">{ord.order_type}</td>
                      <td className="py-2.5 text-center">
                        <span
                          className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider ${
                            ord.status === "FILLED"
                              ? "bg-emerald-950/80 text-emerald-400 border border-emerald-800"
                              : ord.status === "CANCELLED"
                              ? "bg-slate-800 text-slate-400"
                              : "bg-blue-950/80 text-blue-400 border border-blue-800"
                          }`}
                        >
                          {ord.status}
                        </span>
                      </td>
                      <td className="py-2.5 text-right text-slate-400 text-[11px] tabular-nums">
                        {formatISTTime(ord.submitted_at)}
                      </td>
                      <td className="py-2.5 text-center">
                        <button
                          onClick={() => setSelectedOrder(ord)}
                          className="p-1 hover:bg-[#142034] text-slate-400 hover:text-cyan-400 rounded transition-colors"
                          title="Inspect Order State & Fills"
                        >
                          <Eye className="w-3.5 h-3.5" />
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

      {/* ORDER DETAIL INSPECTOR MODAL */}
      {selectedOrder && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-xl rounded-lg border border-[#1a2336] bg-[#090d14] shadow-2xl p-5 font-mono text-xs space-y-4">
            <div className="flex items-center justify-between border-b border-[#1a2336] pb-3">
              <div className="flex items-center space-x-2">
                <ListOrdered className="w-4 h-4 text-cyan-400" />
                <span className="font-bold text-slate-100 text-sm">
                  ORDER LIFECYCLE: {selectedOrder.client_order_id}
                </span>
              </div>
              <button
                onClick={() => setSelectedOrder(null)}
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Lifecycle Timeline */}
            <div className="p-3 rounded bg-[#0d121c] border border-[#1a2336] space-y-2">
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                State Machine Lifecycle
              </div>
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-emerald-400">INTENT_CREATED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400">RISK_VALIDATED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400">BROKER_ACCEPTED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400 font-bold bg-emerald-950/70 px-2 py-0.5 rounded border border-emerald-800">
                  {selectedOrder.status}
                </span>
              </div>
            </div>

            {/* Order Attributes Grid */}
            <div className="grid grid-cols-2 gap-2.5 text-slate-300">
              <div className="p-2.5 rounded bg-[#0d121c] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500">Broker Reference</div>
                <div className="font-bold text-slate-200 mt-0.5">{selectedOrder.broker_order_id || "None"}</div>
              </div>
              <div className="p-2.5 rounded bg-[#0d121c] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500">Contract Symbol</div>
                <div className="font-bold text-slate-200 mt-0.5">{selectedOrder.symbol}</div>
              </div>
              <div className="p-2.5 rounded bg-[#0d121c] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500">Side & Type</div>
                <div className="font-bold text-slate-200 mt-0.5">
                  {selectedOrder.side} | {selectedOrder.order_type}
                </div>
              </div>
              <div className="p-2.5 rounded bg-[#0d121c] border border-[#1a2336]">
                <div className="text-[10px] text-slate-500">Fill Ratio</div>
                <div className="font-bold text-slate-200 mt-0.5 tabular-nums">
                  {selectedOrder.filled_qty} / {selectedOrder.qty} Filled
                </div>
              </div>
            </div>

            {/* Execution Fills */}
            <div className="space-y-2 pt-1">
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                Matched Execution Fills ({selectedOrder.fills.length})
              </div>
              {selectedOrder.fills.length === 0 ? (
                <div className="p-3 text-center text-slate-500 bg-[#0d121c] rounded border border-[#1a2336]">
                  No fills recorded for this order yet.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {selectedOrder.fills.map((f) => (
                    <div
                      key={f.fill_id}
                      className="p-2.5 rounded bg-[#0d121c] border border-[#1a2336] flex justify-between items-center text-[11px]"
                    >
                      <div>
                        <span className="font-bold text-slate-200">{f.fill_id}</span>
                        <span className="text-slate-500 ml-2">Qty: {f.fill_qty}</span>
                      </div>
                      <div className="text-right">
                        <span className="font-bold text-emerald-400 tabular-nums">₹{formatNumber(f.fill_price, 2)}</span>
                        <span className="text-slate-500 ml-2 tabular-nums">{formatISTTime(f.timestamp)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="pt-2 flex justify-end">
              <button
                onClick={() => setSelectedOrder(null)}
                className="btn-secondary text-xs"
              >
                Close Inspector
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
