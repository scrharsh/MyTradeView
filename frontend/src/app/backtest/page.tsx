"use client";

import React, { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import {
  FlaskConical,
  Play,
  CheckCircle2,
  AlertTriangle,
  Upload,
  FileText,
  BarChart2,
  TrendingUp,
  TrendingDown,
  Layers,
  ArrowRight,
  GitCompare,
} from "lucide-react";
import { api } from "../../lib/api";
import {
  formatINR,
  formatNumber,
  formatPercent,
  getPnlColor,
  formatISTTime,
  formatISTDate,
} from "../../lib/formatters";
import {
  BacktestDetailsResponse,
  BacktestSummaryResponse,
  BenchmarkResponse,
  StrategyConfigVersionResponse,
} from "../../types/api";

const EquityChart = dynamic(
  () => import("../../components/EquityChart").then((m) => m.EquityChart),
  { ssr: false }
);

export default function BacktestPage() {
  const [configs, setConfigs] = useState<StrategyConfigVersionResponse[]>([]);
  const [selectedConfigId, setSelectedConfigId] = useState<string>("");
  const [startDate, setStartDate] = useState("2026-11-06");
  const [endDate, setEndDate] = useState("2026-11-06");
  const [initialCapital, setInitialCapital] = useState(100000);
  const [slippagePoints, setSlippagePoints] = useState(0.5);

  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<"results" | "benchmark">("results");
  const [activeRun, setActiveRun] = useState<BacktestDetailsResponse | null>(null);
  const [runsList, setRunsList] = useState<BacktestSummaryResponse[]>([]);

  // StockMock comparison
  const [csvContent, setCsvContent] = useState<string>("");
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResponse | null>(null);
  const [isBenchmarking, setIsBenchmarking] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);

  const loadInitial = async () => {
    try {
      const [cRes, rRes] = await Promise.allSettled([
        api.getStrategyConfigs(),
        api.getBacktestRuns(),
      ]);
      if (cRes.status === "fulfilled") {
        const cfgs = Array.isArray(cRes.value) ? cRes.value : [];
        setConfigs(cfgs);
        if (cfgs.length > 0) setSelectedConfigId(cfgs[0].config_id);
      }
      if (rRes.status === "fulfilled") {
        const runs = Array.isArray(rRes.value) ? rRes.value : [];
        setRunsList(runs);
        if (runs.length > 0) {
          const latest = await api.getBacktestRun(runs[0].run_id);
          setActiveRun(latest);
        }
      }
    } catch (err) {
      console.error("Backtest load error:", err);
    }
  };

  useEffect(() => {
    loadInitial();
  }, []);

  const handleLaunchBacktest = async () => {
    if (!selectedConfigId) return;
    setIsRunning(true);
    try {
      const run = await api.runBacktest({
        config_id: selectedConfigId,
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
        slippage_points: slippagePoints,
      });
      setActiveRun(run);
      const rList = await api.getBacktestRuns();
      setRunsList(Array.isArray(rList) ? rList : []);
      setActiveTab("results");
    } catch (err: any) {
      alert(`Backtest execution error: ${err?.message}`);
    } finally {
      setIsRunning(false);
    }
  };

  const handleRunBenchmark = async () => {
    if (!activeRun) return;
    setIsBenchmarking(true);
    setBenchmarkError(null);
    try {
      const result = await api.benchmarkStockMock(activeRun.summary.run_id, csvContent);
      setBenchmarkResult(result);
    } catch (err: any) {
      setBenchmarkError(err?.message || "Failed to compare StockMock benchmark");
    } finally {
      setIsBenchmarking(false);
    }
  };

  const handleLoadSampleStockMock = () => {
    const sample = `Date,Strategy,Symbol,Type,Entry Time,Entry Price,Exit Time,Exit Price,Profit,Status
2026-11-06,NIFTY_ORB,NSE:NIFTY26NOV25000CE,BUY,09:46:00,143.00,10:15:00,154.50,862.50,CLOSED`;
    setCsvContent(sample);
  };

  const summary = activeRun?.summary;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-4 gap-3">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <FlaskConical className="w-5 h-5 text-cyan-400" />
            <span>DISCRETE-EVENT BACKTESTING LAB</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Deterministic simulation engine reproducing StockMock ORB mechanics with realistic fills.
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center space-x-1 bg-[#121824] p-1 rounded border border-[#1f293d] font-mono text-xs">
          <button
            onClick={() => setActiveTab("results")}
            className={`px-3 py-1.5 rounded transition-colors ${
              activeTab === "results"
                ? "bg-cyan-600 text-white font-bold"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Simulation Results & Analytics
          </button>
          <button
            onClick={() => setActiveTab("benchmark")}
            className={`px-3 py-1.5 rounded transition-colors ${
              activeTab === "benchmark"
                ? "bg-cyan-600 text-white font-bold"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            StockMock Benchmark Comparison
          </button>
        </div>
      </div>

      {/* Launcher Parameters Panel */}
      <div className="panel p-5 space-y-4">
        <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center justify-between">
          <span>Simulation Parameters</span>
          <span className="text-[10px] text-slate-500 font-normal">
            Discrete-Event Engine (Deterministic)
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 font-mono text-xs">
          <div className="space-y-1">
            <label className="text-slate-400">Strategy Version</label>
            <select
              value={selectedConfigId}
              onChange={(e) => setSelectedConfigId(e.target.value)}
              className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
            >
              {configs.map((c) => (
                <option key={c.config_id} value={c.config_id}>
                  v{c.version}: {c.underlying_symbol}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">Initial Capital (₹)</label>
            <input
              type="number"
              value={initialCapital}
              onChange={(e) => setInitialCapital(Number(e.target.value))}
              className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="flex items-end">
            <button
              onClick={handleLaunchBacktest}
              disabled={isRunning}
              className={`w-full flex items-center justify-center space-x-2 py-2 px-4 rounded text-xs font-mono font-bold transition-colors ${
                isRunning
                  ? "bg-slate-800 text-slate-500 cursor-not-allowed"
                  : "bg-cyan-600 hover:bg-cyan-500 text-white shadow-md shadow-cyan-950/50"
              }`}
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{isRunning ? "SIMULATING..." : "RUN BACKTEST"}</span>
            </button>
          </div>
        </div>
      </div>

      {activeTab === "results" ? (
        <div className="space-y-6">
          {/* KPI Metrics Summary Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">NET PROFIT</div>
              <div className={`text-base font-bold mt-1 ${getPnlColor(summary?.net_profit || 0)}`}>
                {formatINR(summary?.net_profit || 0)}
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">WIN RATE</div>
              <div className="text-base font-bold text-slate-100 mt-1">
                {formatPercent(summary?.win_rate_pct || 0)}
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">PROFIT FACTOR</div>
              <div className="text-base font-bold text-slate-100 mt-1">
                {formatNumber(summary?.profit_factor || 0, 2)}
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">MAX DRAWDOWN</div>
              <div className="text-base font-bold text-rose-400 mt-1">
                {formatINR(summary?.max_drawdown_amount || 0)} ({formatPercent(summary?.max_drawdown_pct || 0)})
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">TOTAL TRADES</div>
              <div className="text-base font-bold text-slate-100 mt-1">
                {summary?.total_trades || 0}
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">EXPECTANCY</div>
              <div className="text-base font-bold text-emerald-400 mt-1">
                {formatINR(summary?.expectancy || 0)}
              </div>
            </div>

            <div className="panel p-3 font-mono">
              <div className="text-[10px] text-slate-400">SHARPE RATIO</div>
              <div className="text-base font-bold text-slate-100 mt-1">
                {formatNumber(summary?.sharpe_ratio || 0, 2)}
              </div>
            </div>
          </div>

          {/* Interactive Equity Curve Chart */}
          <div className="panel p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
              <div className="flex items-center space-x-2">
                <BarChart2 className="w-4 h-4 text-cyan-400" />
                <span className="font-bold text-slate-200">PORTFOLIO EQUITY CURVE (INR)</span>
              </div>
              <span className="text-[10px] text-slate-500">
                Run ID: {summary?.run_id || "None"}
              </span>
            </div>

            {activeRun?.equity_curve && activeRun.equity_curve.length > 0 ? (
              <EquityChart equityCurve={activeRun.equity_curve} />
            ) : (
              <div className="h-48 flex items-center justify-center font-mono text-xs text-slate-500">
                No equity curve data available. Launch a backtest to generate.
              </div>
            )}
          </div>

          {/* Trade Journal Table */}
          <div className="panel p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
              <span className="font-bold text-slate-200 uppercase tracking-wider">
                Simulated Trade Journal ({activeRun?.trades?.length || 0})
              </span>
              <span className="text-[10px] text-slate-500">
                Includes slippage model & ₹40/turn broker fees
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-[#1c2638]">
                    <th className="pb-2 font-normal">Trade ID</th>
                    <th className="pb-2 font-normal">Instrument</th>
                    <th className="pb-2 font-normal">Side</th>
                    <th className="pb-2 font-normal text-right">Entry Time</th>
                    <th className="pb-2 font-normal text-right">Entry Price</th>
                    <th className="pb-2 font-normal text-right">Exit Time</th>
                    <th className="pb-2 font-normal text-right">Exit Price</th>
                    <th className="pb-2 font-normal">Exit Reason</th>
                    <th className="pb-2 font-normal text-right">Costs</th>
                    <th className="pb-2 font-normal text-right">Net P&L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#162030]">
                  {(!activeRun?.trades || activeRun.trades.length === 0) ? (
                    <tr>
                      <td colSpan={10} className="py-6 text-center text-slate-500">
                        No trades executed in this simulation period.
                      </td>
                    </tr>
                  ) : (
                    activeRun.trades.map((t) => (
                      <tr key={t.trade_id} className="hover:bg-[#121824]/50">
                        <td className="py-2.5 font-bold text-slate-300">{t.trade_id}</td>
                        <td className="py-2.5 text-slate-200">{t.symbol}</td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400">
                            {t.side}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-400">{formatISTTime(t.entry_time)}</td>
                        <td className="py-2.5 text-right text-slate-200 font-medium">
                          {formatNumber(t.entry_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-400">{formatISTTime(t.exit_time)}</td>
                        <td className="py-2.5 text-right text-slate-200 font-medium">
                          {formatNumber(t.exit_price, 2)}
                        </td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300">
                            {t.exit_reason}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-400">
                          {formatINR(t.transaction_costs)}
                        </td>
                        <td className={`py-2.5 text-right font-bold ${getPnlColor(t.net_pnl)}`}>
                          {formatINR(t.net_pnl)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        /* StockMock Benchmark Comparison Tab */
        <div className="space-y-6">
          <div className="panel p-5 space-y-4">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <GitCompare className="w-4 h-4 text-cyan-400" />
                <span>StockMock CSV Benchmark Comparison</span>
              </div>
              <button
                onClick={handleLoadSampleStockMock}
                className="text-[10px] text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                Load Sample Benchmark CSV
              </button>
            </div>

            <p className="text-xs font-mono text-slate-400">
              Paste or upload exported trades from StockMock to perform automated trade-by-trade
              verification, entry/exit timestamp alignment, and profit-trailing discrepancy checks.
            </p>

            <div className="space-y-2">
              <textarea
                rows={6}
                value={csvContent}
                onChange={(e) => setCsvContent(e.target.value)}
                placeholder="Paste StockMock CSV data here (Date, Strategy, Symbol, Type, Entry Time, Entry Price...)"
                className="w-full rounded bg-[#121824] border border-[#1f293d] p-3 text-xs font-mono text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-cyan-500"
              />
            </div>

            {benchmarkError && (
              <div className="rounded border border-rose-500/40 bg-rose-950/40 p-3 text-xs font-mono text-rose-300">
                {benchmarkError}
              </div>
            )}

            <div className="flex justify-end">
              <button
                onClick={handleRunBenchmark}
                disabled={!csvContent.trim() || isBenchmarking || !activeRun}
                className={`flex items-center space-x-1.5 px-4 py-2 rounded text-xs font-mono font-bold transition-colors ${
                  csvContent.trim() && !isBenchmarking && activeRun
                    ? "bg-cyan-600 hover:bg-cyan-500 text-white"
                    : "bg-slate-800 text-slate-500 cursor-not-allowed"
                }`}
              >
                <GitCompare className="w-3.5 h-3.5" />
                <span>{isBenchmarking ? "BENCHMARKING..." : "COMPARE AGAINST SIMULATION"}</span>
              </button>
            </div>
          </div>

          {/* Benchmark Results */}
          {benchmarkResult && (
            <div className="panel p-5 space-y-4">
              <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center justify-between">
                <span>Benchmark Verification Report</span>
                <span className="text-emerald-400 font-bold">
                  Matched Trades: {benchmarkResult.matched_trades_count} | Discrepancies:{" "}
                  {benchmarkResult.discrepancy_count}
                </span>
              </div>

              {/* Delta Summary KPI */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
                <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
                  <div className="text-[10px] text-slate-400">NET P&L DELTA</div>
                  <div className={`text-sm font-bold mt-1 ${getPnlColor(benchmarkResult.pnl_diff)}`}>
                    {formatINR(benchmarkResult.pnl_diff)}
                  </div>
                </div>
                <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
                  <div className="text-[10px] text-slate-400">BENCHMARK FIDELITY</div>
                  <div className="text-sm font-bold text-emerald-400 mt-1">
                    {benchmarkResult.is_matched ? "100% IDENTICAL" : "DIVERGENCES FOUND"}
                  </div>
                </div>
                <div className="p-3 rounded bg-[#121824] border border-[#1f293d]">
                  <div className="text-[10px] text-slate-400">SUMMARY</div>
                  <div className="text-xs font-medium text-slate-200 mt-1">
                    {benchmarkResult.summary}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
