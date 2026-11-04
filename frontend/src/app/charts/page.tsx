"use client";

import React, { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { TrendingUp, RefreshCw, Layers, Calendar, BarChart2, ShieldCheck } from "lucide-react";
import { api } from "../../lib/api";
import { CandleItem } from "../../types/api";

// Dynamically import TradingChart with ssr: false for lightweight-charts canvas
const TradingChart = dynamic(
  () => import("../../components/TradingChart").then((mod) => mod.TradingChart),
  { ssr: false }
);

export default function ChartsPage() {
  const [spotCandles, setSpotCandles] = useState<CandleItem[]>([]);
  const [optionCandles, setOptionCandles] = useState<CandleItem[]>([]);
  const [isSynthetic, setIsSynthetic] = useState(false);
  const [provenance, setProvenance] = useState("DATASET");
  const [loading, setLoading] = useState(true);
  const [timeframe, setTimeframe] = useState("1m");
  const [date, setDate] = useState("2026-11-02");
  const [orbHigh, setOrbHigh] = useState<number | undefined>(undefined);
  const [orbLow, setOrbLow] = useState<number | undefined>(undefined);
  const [activeContract, setActiveContract] = useState<string>("NSE:NIFTY26NOV25000CE");
  const [optionEntryPrice, setOptionEntryPrice] = useState<number | undefined>(undefined);

  const loadCandles = async () => {
    setLoading(true);
    try {
      const [spotRes, optRes, sessRes, posRes] = await Promise.allSettled([
        api.getCandles("NSE:NIFTY50-INDEX", timeframe),
        api.getCandles("NSE:NIFTY26NOV25000CE", timeframe),
        api.getActiveSession(),
        api.getPositions(),
      ]);
      if (spotRes.status === "fulfilled") {
        setSpotCandles(spotRes.value.candles);
        setIsSynthetic(Boolean(spotRes.value.is_synthetic));
        setProvenance(spotRes.value.provenance || "HISTORICAL_REPLAY");
      }
      if (optRes.status === "fulfilled") {
        setOptionCandles(optRes.value.candles);
      }
      if (sessRes.status === "fulfilled") {
        const sess = sessRes.value;
        if (sess.trading_date) setDate(sess.trading_date);
        if (sess.opening_range?.is_established && sess.opening_range.high && sess.opening_range.low) {
          setOrbHigh(Number(sess.opening_range.high));
          setOrbLow(Number(sess.opening_range.low));
        } else {
          setOrbHigh(undefined);
          setOrbLow(undefined);
        }
        if (sess.active_contract_symbol) {
          setActiveContract(sess.active_contract_symbol);
        }
      }
      if (posRes.status === "fulfilled") {
        const openPos = posRes.value.find((p) => p.is_open && p.net_qty !== 0);
        if (openPos) {
          setOptionEntryPrice(Number(openPos.avg_buy_price || 0));
        } else {
          setOptionEntryPrice(undefined);
        }
      }
    } catch (err) {
      console.error("Failed to load candles:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCandles();
  }, [timeframe]);

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>MARKET DATA</span>
            <span>/</span>
            <span>CANVAS</span>
            <span>/</span>
            <span className="text-cyan-400">FINANCIAL CHARTS</span>
          </div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <TrendingUp className="w-5 h-5 text-cyan-400" />
            <span>INTERACTIVE FINANCIAL CHARTS</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Dual-panel canvas: NIFTY 50 Spot with 09:16-09:45 ORB boundaries & Option derivative execution.
          </p>
        </div>

        {/* Toolbar Controls */}
        <div className="flex items-center space-x-3 font-mono text-xs">
          <div className="flex items-center space-x-1 bg-[#090d14] p-1 rounded border border-[#1a2336]">
            {["1m", "5m", "15m"].map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-2.5 py-1 rounded text-xs transition-all ${
                  timeframe === tf
                    ? "bg-[#142034] text-cyan-300 font-bold border border-cyan-500/40 shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>

          <div className="flex items-center space-x-1.5 bg-[#090d14] px-3 py-1.5 rounded border border-[#1a2336] text-slate-300 text-xs">
            <Calendar className="w-3.5 h-3.5 text-slate-400" />
            <span>{date} (Session)</span>
          </div>

          <button
            onClick={loadCandles}
            className="btn-secondary text-xs flex items-center space-x-1.5"
            title="Refresh Candlestick Feeds"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Synthetic Dataset Warning / Provenance Banner */}
      {isSynthetic && (
        <div className="rounded border border-amber-500/40 bg-amber-950/20 p-3 text-xs font-mono text-amber-300 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            <span className="px-2 py-0.5 rounded bg-amber-950/80 border border-amber-600/40 text-[10px] font-bold">
              [DATASET: SYNTHETIC SIMULATION ({date})]
            </span>
            <span>Historical market replay generated for deterministic NIFTY ORB backtesting & verification.</span>
          </div>
          <span className="text-[10px] text-amber-400 font-bold uppercase tracking-wider">{provenance}</span>
        </div>
      )}

      {/* Chart Canvas */}
      {loading ? (
        <div className="panel h-96 flex flex-col items-center justify-center space-y-3 font-mono text-xs text-slate-400">
          <RefreshCw className="w-6 h-6 animate-spin text-cyan-400" />
          <span>Loading candlestick feeds from discrete dataset...</span>
        </div>
      ) : (
        <div className="space-y-4">
          {optionCandles.length === 0 && (
            <div className="rounded border border-blue-500/30 bg-blue-950/20 p-2.5 text-xs font-mono text-blue-300">
              Option derivative candles (NSE:NIFTY26NOV25000CE) are unavailable for this interval. Spot chart is shown.
            </div>
          )}
          <div className="panel p-3">
            <TradingChart
              spotCandles={spotCandles}
              optionCandles={optionCandles}
              orbHigh={orbHigh}
              orbLow={orbLow}
              optionEntryPrice={optionEntryPrice}
              optionSymbol={activeContract}
            />
          </div>
        </div>
      )}
    </div>
  );
}
