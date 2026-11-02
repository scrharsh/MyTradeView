"use client";

import React, { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { TrendingUp, RefreshCw, Layers, Calendar, BarChart2 } from "lucide-react";
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
  const [loading, setLoading] = useState(true);
  const [timeframe, setTimeframe] = useState("1m");
  const [date, setDate] = useState("2026-11-06");

  const loadCandles = async () => {
    setLoading(true);
    try {
      const [spotRes, optRes] = await Promise.allSettled([
        api.getCandles("NSE:NIFTY50-INDEX", timeframe),
        api.getCandles("NSE:NIFTY26NOV25000CE", timeframe),
      ]);
      if (spotRes.status === "fulfilled") setSpotCandles(spotRes.value.candles);
      if (optRes.status === "fulfilled") setOptionCandles(optRes.value.candles);
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
      {/* Chart Control Toolbar */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-4 gap-3">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <TrendingUp className="w-5 h-5 text-cyan-400" />
            <span>INTERACTIVE FINANCIAL CHARTS</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Dual-panel canvas: NIFTY 50 Spot with 09:16-09:45 ORB boundaries & Option derivative execution.
          </p>
        </div>

        {/* Toolbar Controls */}
        <div className="flex items-center space-x-3 font-mono text-xs">
          <div className="flex items-center space-x-1 bg-[#121824] p-1 rounded border border-[#1f293d]">
            {["1m", "5m", "15m"].map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-2.5 py-1 rounded text-xs transition-colors ${
                  timeframe === tf
                    ? "bg-cyan-600 text-white font-bold"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>

          <div className="flex items-center space-x-1 bg-[#121824] px-2.5 py-1.5 rounded border border-[#1f293d] text-slate-300">
            <Calendar className="w-3.5 h-3.5 text-slate-400" />
            <span>{date} (Bullish Breakout Sample)</span>
          </div>

          <button
            onClick={loadCandles}
            className="flex items-center space-x-1 px-3 py-1.5 bg-[#121824] hover:bg-[#1a2334] text-slate-300 border border-[#1f293d] rounded transition-colors"
            title="Refresh Candles"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Chart Canvas */}
      {loading ? (
        <div className="panel h-96 flex flex-col items-center justify-center space-y-3 font-mono text-xs text-slate-400">
          <RefreshCw className="w-6 h-6 animate-spin text-cyan-400" />
          <span>Loading historical candlestick feeds...</span>
        </div>
      ) : (
        <TradingChart
          spotCandles={spotCandles}
          optionCandles={optionCandles}
          orbHigh={25045.5}
          orbLow={24962.1}
          breakoutTime="2026-11-06T09:46:00+05:30"
          optionEntryPrice={143.0}
        />
      )}
    </div>
  );
}

