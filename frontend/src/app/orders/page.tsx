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

  return (
    <div className="space-y-6">
      {/* Page Title & Controls */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-4 gap-3">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <ListOrdered className="w-5 h-5 text-cyan-400" />
            <span>ORDERS & POSITIONS AUDIT</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Complete order lifecycle history, partial fills, and real-time position square-off controls.
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

      {/* POSITIONS SECTION */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between border-b border-[#1c2638] pb-3">
          <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
            Open Positions Portfolio ({positions.length})
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            Execution Mode: Strictly PAPER
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1c2638]">
                <th className="pb-2 font-normal">Position ID</th>
                <th className="pb-2 font-normal">Instrument</th>
                <th className="pb-2 font-normal">Side</th>
                <th className="pb-2 font-normal text-right">Net Qty</th>
                <th className="pb-2 font-normal text-right">Avg Entry</th>
                <th className="pb-2 font-normal text-right">LTP</th>
                <th className="pb-2 font-normal text-right">Realized</th>
                <th className="pb-2 font-normal text-right">Unrealized</th>
                <th className="pb-2 font-normal text-right">Total P&L</th>
                <th className="pb-2 font-normal text-center">Liquidation</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#162030]">
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-6 text-center text-slate-500">
                    No open positions at this time.
                  </td>
                </tr>
              ) : (
                positions.map((p) => {
                  const isLong = p.net_qty > 0;
                  const total = Number(p.total_pnl);
                  return (
                    <tr key={p.position_id} className="hover:bg-[#121824]/50">
                      <td className="py-2.5 font-bold text-slate-300">{p.position_id}</td>
                      <td className="py-2.5 font-semibold text-slate-200">{p.symbol}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${isLong ? "text-emerald-400 bg-emerald-950/60" : "text-rose-400 bg-rose-950/60"
                            }`}
                        >
                          {isLong ? "LONG" : "SHORT"}
                        </span>
                      </td>
                      <td className="py-2.5 text-right font-medium">{Math.abs(p.net_qty)}</td>
                      <td className="py-2.5 text-right text-slate-300">
                        {formatNumber(p.avg_buy_price, 2)}
                      </td>
                      <td className="py-2.5 text-right font-bold text-slate-100">
                        {formatNumber(p.current_ltp, 2)}
                      </td>
                      <td className="py-2.5 text-right text-slate-400">{formatINR(p.realized_pnl)}</td>
                      <td className={`py-2.5 text-right ${getPnlColor(p.unrealized_pnl)}`}>
                        {formatINR(p.unrealized_pnl)}
                      </td>
                      <td className={`py-2.5 text-right font-bold ${getPnlColor(total)}`}>
                        {formatINR(total)}
                      </td>
                      <td className="py-2.5 text-center">
                        <button
                          onClick={() => handleSquareOff(p.symbol)}
                          disabled={squaringOff === p.symbol}
                          className="px-2.5 py-1 bg-rose-950/60 hover:bg-rose-900 text-rose-300 border border-rose-800 rounded text-[10px] font-semibold transition-colors"
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
      <div className="panel p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-3 gap-3">
          <span className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider">
            Order Lifecycle Management ({filteredOrders.length})
          </span>

          <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
            {/* Status Tabs */}
            <div className="flex items-center space-x-1 bg-[#121824] p-1 rounded border border-[#1f293d]">
              {["ALL", "FILLED", "OPEN", "CANCELLED", "REJECTED"].map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  className={`px-2.5 py-1 rounded text-[11px] transition-colors ${statusFilter === st
                      ? "bg-cyan-600 text-white font-bold"
                      : "text-slate-400 hover:text-slate-200"
                    }`}
                >
                  {st}
                </button>
              ))}
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-500" />
              <input
                type="text"
                placeholder="Search Client ID / Symbol..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1.5 rounded bg-[#121824] border border-[#1f293d] text-xs font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-[#1c2638]">
                <th className="pb-2 font-normal">Client Order ID</th>
                <th className="pb-2 font-normal">Broker Order ID</th>
                <th className="pb-2 font-normal">Instrument</th>
                <th className="pb-2 font-normal">Side</th>
                <th className="pb-2 font-normal text-right">Qty</th>
                <th className="pb-2 font-normal text-right">Filled</th>
                <th className="pb-2 font-normal">Type</th>
                <th className="pb-2 font-normal text-center">Status</th>
                <th className="pb-2 font-normal text-right">Time</th>
                <th className="pb-2 font-normal text-center">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#162030]">
              {filteredOrders.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-6 text-center text-slate-500">
                    No orders matching selected criteria.
                  </td>
                </tr>
              ) : (
                filteredOrders.map((ord) => {
                  const isBuy = ord.side === "BUY";
                  return (
                    <tr key={ord.client_order_id} className="hover:bg-[#121824]/50">
                      <td className="py-2.5 font-bold text-slate-300">{ord.client_order_id}</td>
                      <td className="py-2.5 text-slate-400 text-[11px] font-mono">
                        {ord.broker_order_id || "—"}
                      </td>
                      <td className="py-2.5 font-semibold text-slate-200">{ord.symbol}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${isBuy ? "text-emerald-400 bg-emerald-950/60" : "text-rose-400 bg-rose-950/60"
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
                          className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider ${ord.status === "FILLED"
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
                      <td className="py-2.5 text-center">
                        <button
                          onClick={() => setSelectedOrder(ord)}
                          className="p-1 hover:bg-[#1a2334] text-slate-400 hover:text-cyan-400 rounded transition-colors"
                          title="View Order Details & Execution Fills"
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
          <div className="w-full max-w-xl rounded-lg border border-[#1e293b] bg-[#0c1017] shadow-2xl p-6 font-mono text-xs space-y-4">
            <div className="flex items-center justify-between border-b border-[#1c2638] pb-3">
              <div className="flex items-center space-x-2">
                <ListOrdered className="w-4 h-4 text-cyan-400" />
                <span className="font-bold text-slate-100 text-sm">
                  ORDER LIFECYCLE: {selectedOrder.client_order_id}
                </span>
              </div>
              <button
                onClick={() => setSelectedOrder(null)}
                className="text-slate-400 hover:text-slate-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Lifecycle Timeline */}
            <div className="p-3 rounded bg-[#121824] border border-[#1f293d] space-y-2">
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                State Machine Lifecycle
              </div>
              <div className="flex items-center space-x-2 text-[11px]">
                <span className="text-emerald-400">INTENT_CREATED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400">RISK_VALIDATED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400">BROKER_ACCEPTED</span>
                <span className="text-slate-600">→</span>
                <span className="text-emerald-400 font-bold">{selectedOrder.status}</span>
              </div>
            </div>

            {/* Order Attributes Grid */}
            <div className="grid grid-cols-2 gap-3 text-slate-300">
              <div className="p-2.5 rounded bg-[#121824]">
                <div className="text-[10px] text-slate-500">Broker ID</div>
                <div className="font-bold text-slate-200">{selectedOrder.broker_order_id || "None"}</div>
              </div>
              <div className="p-2.5 rounded bg-[#121824]">
                <div className="text-[10px] text-slate-500">Instrument</div>
                <div className="font-bold text-slate-200">{selectedOrder.symbol}</div>
              </div>
              <div className="p-2.5 rounded bg-[#121824]">
                <div className="text-[10px] text-slate-500">Side & Type</div>
                <div className="font-bold text-slate-200">
                  {selectedOrder.side} | {selectedOrder.order_type}
                </div>
              </div>
              <div className="p-2.5 rounded bg-[#121824]">
                <div className="text-[10px] text-slate-500">Quantities</div>
                <div className="font-bold text-slate-200">
                  {selectedOrder.filled_qty} / {selectedOrder.qty} Filled
                </div>
              </div>
            </div>

            {/* Execution Fills */}
            <div className="space-y-2 pt-2">
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                Matched Execution Fills ({selectedOrder.fills.length})
              </div>
              {selectedOrder.fills.length === 0 ? (
                <div className="p-3 text-center text-slate-500 bg-[#121824] rounded">
                  No fills recorded for this order yet.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {selectedOrder.fills.map((f) => (
                    <div
                      key={f.fill_id}
                      className="p-2.5 rounded bg-[#121824] border border-[#1f293d] flex justify-between items-center text-[11px]"
                    >
                      <div>
                        <span className="font-bold text-slate-200">{f.fill_id}</span>
                        <span className="text-slate-500 ml-2">Qty: {f.fill_qty}</span>
                      </div>
                      <div className="text-right">
                        <span className="font-bold text-emerald-400">₹{formatNumber(f.fill_price, 2)}</span>
                        <span className="text-slate-500 ml-2">{formatISTTime(f.timestamp)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="pt-2 flex justify-end">
              <button
                onClick={() => setSelectedOrder(null)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded font-semibold"
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