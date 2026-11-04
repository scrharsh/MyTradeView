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
  Calendar,
  DollarSign,
  Percent,
  Activity,
  Check,
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
  const [datasetId, setDatasetId] = useState("HISTORICAL_SPOT_8D");
  const [startDate, setStartDate] = useState("2026-10-22");
  const [endDate, setEndDate] = useState("2026-11-02");
  const [initialCapital, setInitialCapital] = useState(100000);
  const [slippagePoints, setSlippagePoints] = useState(0.5);
  const [brokeragePerOrder, setBrokeragePerOrder] = useState(20);

  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<"results" | "benchmark">("results");
  const [activeRun, setActiveRun] = useState<BacktestDetailsResponse | null>(null);
  const [runsList, setRunsList] = useState<BacktestSummaryResponse[]>([]);

  // StockMock comparison
  const [csvContent, setCsvContent] = useState<string>("");
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResponse | null>(null);
  const [isBenchmarking, setIsBenchmarking] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);

  const handleDatasetChange = (newDs: string) => {
    setDatasetId(newDs);
    if (newDs === "HISTORICAL_SPOT_8D") {
      setStartDate("2026-10-22");
      setEndDate("2026-11-02");
    } else {
      setStartDate("2026-11-06");
      setEndDate("2026-11-06");
    }
  };

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
        dataset_id: datasetId,
        start_date: startDate,
        end_date: endDate,
        initial_capital: initialCapital,
        slippage_points: slippagePoints,
        brokerage_per_order: brokeragePerOrder,
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
2026-11-02,NIFTY_ORB,NSE:NIFTY26NOV25000CE,BUY,09:46:00,143.00,10:15:00,154.50,862.50,CLOSED`;
    setCsvContent(sample);
  };

  const summary = activeRun?.summary;

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>QUANT LAB</span>
            <span>/</span>
            <span>DISCRETE-EVENT ENGINE</span>
            <span>/</span>
            <span className="text-cyan-400">STOCKMOCK SIMULATION</span>
          </div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <FlaskConical className="w-5 h-5 text-cyan-400" />
            <span>DISCRETE-EVENT BACKTESTING LAB</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Tick-accurate simulation engine reproducing StockMock NIFTY ORB mechanics, real fills, and slippage.
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center space-x-1 bg-[#090d14] p-1 rounded border border-[#1a2336] font-mono text-xs">
          <button
            onClick={() => setActiveTab("results")}
            className={`px-3 py-1.5 rounded transition-all ${
              activeTab === "results"
                ? "bg-[#142034] text-cyan-300 font-bold border border-cyan-500/40 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Simulation Results & Analytics
          </button>
          <button
            onClick={() => setActiveTab("benchmark")}
            className={`px-3 py-1.5 rounded transition-all ${
              activeTab === "benchmark"
                ? "bg-[#142034] text-cyan-300 font-bold border border-cyan-500/40 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            StockMock Benchmark Comparison
          </button>
        </div>
      </div>

      {/* Launcher Parameters Panel */}
      <div className="panel p-4 space-y-3.5">
        <div className="panel-header">
          <div className="flex items-center space-x-2">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
            <span>Simulation Parameters & Model Inputs</span>
          </div>
          <span className="text-[10px] text-slate-500 font-normal">
            Discrete-Event Order Matching Model
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-6 gap-3 font-mono text-xs">
          <div className="space-y-1">
            <label className="text-slate-400 font-medium">Dataset Source</label>
            <select
              value={datasetId}
              onChange={(e) => handleDatasetChange(e.target.value)}
              className="select-field text-slate-100"
            >
              <option value="HISTORICAL_SPOT_8D">HISTORICAL: 1m Spot History (2026-10-22 to 2026-11-02)</option>
              <option value="DEFAULT_SYNTHETIC">SYNTHETIC: Benchmark Day (2026-11-06)</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400 font-medium">Strategy Version</label>
            <select
              value={selectedConfigId}
              onChange={(e) => setSelectedConfigId(e.target.value)}
              className="select-field text-slate-100"
            >
              {configs.map((c) => (
                <option key={c.config_id} value={c.config_id}>
                  v{c.version}: {c.underlying_symbol} {c.is_active ? "(Active)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400 font-medium">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="input-field text-slate-100"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400 font-medium">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="input-field text-slate-100"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400 font-medium">Capital, Slip & Brok</label>
            <div className="flex items-center space-x-1">
              <input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(Number(e.target.value))}
                className="input-field text-slate-100 tabular-nums w-1/2"
                title="Initial Capital (INR)"
              />
              <input
                type="number"
                step="0.1"
                value={slippagePoints}
                onChange={(e) => setSlippagePoints(Number(e.target.value))}
                className="input-field text-slate-100 tabular-nums w-1/4"
                title="Slippage (Points)"
              />
              <input
                type="number"
                step="1"
                value={brokeragePerOrder}
                onChange={(e) => setBrokeragePerOrder(Number(e.target.value))}
                className="input-field text-slate-100 tabular-nums w-1/4"
                title="Brokerage/Order (INR)"
              />
            </div>
          </div>

          <div className="flex items-end">
            <button
              onClick={handleLaunchBacktest}
              disabled={isRunning || !selectedConfigId}
              className={`w-full flex items-center justify-center space-x-2 py-2 px-4 rounded text-xs font-mono font-bold transition-all ${
                isRunning
                  ? "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
                  : "btn-primary shadow-lg shadow-cyan-950/50"
              }`}
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{isRunning ? "RUNNING SIMULATION..." : "EXECUTE BACKTEST"}</span>
            </button>
          </div>
        </div>
      </div>

      {activeTab === "results" ? (
        <div className="space-y-5">
          {summary && (
            <div className="space-y-2 font-mono text-xs">
              <div className="flex flex-wrap items-center justify-between panel p-3 border border-[#1a2336] bg-[#090d14]">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                      summary.is_underlying_only
                        ? "bg-purple-950/70 border border-purple-600/40 text-purple-300"
                        : summary.is_synthetic
                        ? "bg-amber-950/70 border border-amber-600/40 text-amber-300"
                        : "bg-blue-950/70 border border-blue-600/40 text-blue-300"
                    }`}
                  >
                    {summary.is_underlying_only
                      ? "UNDERLYING SPOT INDEX REPLAY (POINTS ONLY)"
                      : summary.is_synthetic
                      ? "SYNTHETIC TEST FIXTURE"
                      : "HISTORICAL MARKET REPLAY"}
                  </span>
                  <span className="text-slate-400">
                    Dataset: <strong className="text-slate-200">{summary.dataset_id}</strong>
                  </span>
                  <span className="text-slate-600">|</span>
                  <span className="text-slate-400">
                    Strategy: <strong className="text-slate-200">v{summary.strategy_version}</strong> ({summary.strategy_name})
                  </span>
                </div>
                <div className="text-[10px] text-slate-500 mt-1 sm:mt-0">
                  Window: {summary.start_date} to {summary.end_date} (1m Intraday) | Slippage: {slippagePoints} pts | Brokerage: ₹{brokeragePerOrder * 2}/turn
                </div>
              </div>

              {summary.is_underlying_only && (
                <div className="p-2.5 rounded bg-purple-950/30 border border-purple-800/40 text-[11px] text-purple-200 flex items-center space-x-2">
                  <Activity className="w-3.5 h-3.5 text-purple-400 flex-shrink-0" />
                  <span>
                    UNDERLYING SPOT REPLAY ADVISORY: This backtest simulates breakout signal execution across authentic 1-minute NIFTY 50 cash index candles ({summary.start_date} to {summary.end_date}). Results represent underlying index points and MUST NOT be represented as options contract P&L.
                  </span>
                </div>
              )}

              {summary.is_synthetic && !summary.is_underlying_only && (
                <div className="p-2.5 rounded bg-amber-950/20 border border-amber-800/30 text-[11px] text-amber-300 flex items-center space-x-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                  <span>
                    SYNTHETIC SIMULATION ADVISORY: This backtest was executed against synthetic option candles. Results represent test fixtures and must NOT be represented as strategy performance.
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Institutional KPI Metrics Summary Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5">
            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">NET PROFIT</div>
              <div className={`text-base font-bold mt-1 tabular-nums ${getPnlColor(summary?.net_profit || 0)}`}>
                {formatINR(summary?.net_profit || 0)}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">Post-slippage & brokerage</div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">WIN RATE</div>
              <div className="text-base font-bold text-slate-100 mt-1 tabular-nums">
                {formatPercent(summary?.win_rate_pct || 0)}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">
                {summary
                  ? `${Math.round(((Number(summary.win_rate_pct) || 0) / 100) * (summary.total_trades || 0))}W / ${(summary.total_trades || 0) - Math.round(((Number(summary.win_rate_pct) || 0) / 100) * (summary.total_trades || 0))}L`
                  : "--"}
              </div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">PROFIT FACTOR</div>
              <div className="text-base font-bold text-slate-100 mt-1 tabular-nums">
                {formatNumber(summary?.profit_factor || 0, 2)}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">Gross Wins / Gross Losses</div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">MAX DRAWDOWN</div>
              <div className="text-base font-bold text-rose-400 mt-1 tabular-nums">
                {formatINR(summary?.max_drawdown_amount || 0)}
              </div>
              <div className="text-[9px] text-slate-500 mt-1 tabular-nums">
                {formatPercent(summary?.max_drawdown_pct || 0)} peak-to-trough
              </div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">TOTAL TRADES</div>
              <div className="text-base font-bold text-slate-100 mt-1 tabular-nums">
                {summary?.total_trades || 0}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">1 trade max per session</div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">EXPECTANCY</div>
              <div className={`text-base font-bold mt-1 tabular-nums ${getPnlColor(summary?.expectancy || 0)}`}>
                {formatINR(summary?.expectancy || 0)}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">Per trade edge</div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">SHARPE RATIO</div>
              <div className="text-base font-bold text-slate-100 mt-1 tabular-nums">
                {summary?.sharpe_ratio !== null && summary?.sharpe_ratio !== undefined
                  ? formatNumber(summary.sharpe_ratio, 2)
                  : "N/A"}
              </div>
              <div
                className="text-[9px] text-slate-500 mt-1 truncate"
                title={summary?.sharpe_ratio_reason || "Requires >=2 daily periods"}
              >
                {summary?.sharpe_ratio !== null && summary?.sharpe_ratio !== undefined
                  ? "Annualized (Daily)"
                  : "Requires ≥2 periods"}
              </div>
            </div>

            <div className="panel p-3 font-mono flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">PNL / STDEV</div>
              <div className="text-base font-bold text-slate-100 mt-1 tabular-nums">
                {summary?.trade_pnl_mean_std_ratio !== null && summary?.trade_pnl_mean_std_ratio !== undefined
                  ? formatNumber(summary.trade_pnl_mean_std_ratio, 2)
                  : "N/A"}
              </div>
              <div className="text-[9px] text-slate-500 mt-1">Consistency ratio</div>
            </div>
          </div>

          {/* Interactive Equity Curve Chart */}
          <div className="panel p-4 space-y-3">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <BarChart2 className="w-4 h-4 text-cyan-400" />
                <span>PORTFOLIO EQUITY CURVE (INR)</span>
              </div>
              <span className="text-[10px] text-slate-500 font-mono">
                Run ID: {summary?.run_id || "None"}
              </span>
            </div>

            {activeRun?.equity_curve && activeRun.equity_curve.length > 0 ? (
              <EquityChart equityCurve={activeRun.equity_curve} />
            ) : (
              <div className="h-56 flex flex-col items-center justify-center font-mono text-xs text-slate-500 space-y-2">
                <FlaskConical className="w-6 h-6 text-slate-600" />
                <span>No equity curve data available. Launch a backtest to generate visual trajectory.</span>
              </div>
            )}
          </div>

          {/* Trade Journal Table */}
          <div className="panel p-4 space-y-3">
            <div className="panel-header">
              <span className="uppercase tracking-wider">
                Simulated Trade Journal ({activeRun?.trades?.length || 0})
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                Discrete fills with ₹40/turn brokerage + {slippagePoints} pts slippage
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-[#1a2336] text-[11px]">
                    <th className="pb-2 font-semibold">Trade ID</th>
                    <th className="pb-2 font-semibold">Contract</th>
                    <th className="pb-2 font-semibold">Side</th>
                    <th className="pb-2 font-semibold text-right">Entry Time</th>
                    <th className="pb-2 font-semibold text-right">Entry Price</th>
                    <th className="pb-2 font-semibold text-right">Exit Time</th>
                    <th className="pb-2 font-semibold text-right">Exit Price</th>
                    <th className="pb-2 font-semibold">Exit Reason</th>
                    <th className="pb-2 font-semibold text-right">Costs</th>
                    <th className="pb-2 font-semibold text-right">Net P&L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#131b2a]">
                  {!activeRun?.trades || activeRun.trades.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="py-8 text-center text-slate-500">
                        No trades executed in this simulation period.
                      </td>
                    </tr>
                  ) : (
                    activeRun.trades.map((t) => (
                      <tr key={t.trade_id} className="hover:bg-[#0d121c]/60 transition-colors">
                        <td className="py-2.5 font-bold text-slate-300">{t.trade_id}</td>
                        <td className="py-2.5 font-semibold text-slate-100">{t.symbol}</td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950/70 border border-emerald-800 text-emerald-400">
                            {t.side}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-400 tabular-nums">{formatISTTime(t.entry_time)}</td>
                        <td className="py-2.5 text-right text-slate-200 font-medium tabular-nums">
                          ₹{formatNumber(t.entry_price, 2)}
                        </td>
                        <td className="py-2.5 text-right text-slate-400 tabular-nums">{formatISTTime(t.exit_time)}</td>
                        <td className="py-2.5 text-right text-slate-200 font-medium tabular-nums">
                          ₹{formatNumber(t.exit_price, 2)}
                        </td>
                        <td className="py-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[#121824] border border-[#1f293d] text-slate-300">
                            {t.exit_reason}
                          </span>
                        </td>
                        <td className="py-2.5 text-right text-slate-400 tabular-nums">
                          {formatINR(t.transaction_costs)}
                        </td>
                        <td className={`py-2.5 text-right font-bold tabular-nums ${getPnlColor(t.net_pnl)}`}>
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
        <div className="space-y-5">
          <div className="panel p-4 space-y-4">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <GitCompare className="w-4 h-4 text-cyan-400" />
                <span>StockMock CSV Benchmark Comparison Engine</span>
              </div>
              <button
                onClick={handleLoadSampleStockMock}
                className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono transition-colors"
              >
                Load Sample Benchmark CSV
              </button>
            </div>

            <p className="text-xs font-mono text-slate-400">
              Paste or import trade exports from StockMock to perform trade-by-trade timestamp verification,
              entry/exit fill alignment, and profit-floor trailing discrepancy audits.
            </p>

            <div className="space-y-2">
              <textarea
                rows={6}
                value={csvContent}
                onChange={(e) => setCsvContent(e.target.value)}
                placeholder="Paste StockMock CSV data here (Date, Strategy, Symbol, Type, Entry Time, Entry Price...)"
                className="input-field text-xs font-mono text-slate-100 placeholder:text-slate-600 font-mono"
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
                className={`flex items-center space-x-1.5 px-4 py-2 rounded text-xs font-mono font-bold transition-all ${
                  csvContent.trim() && !isBenchmarking && activeRun
                    ? "btn-primary shadow-lg shadow-cyan-950/50"
                    : "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
                }`}
              >
                <GitCompare className="w-3.5 h-3.5" />
                <span>{isBenchmarking ? "AUDITING DELTAS..." : "COMPARE AGAINST ACTIVE RUN"}</span>
              </button>
            </div>
          </div>

          {/* Benchmark Results */}
          {benchmarkResult && (
            <div className="panel p-4 space-y-4">
              <div className="panel-header">
                <span>Benchmark Verification Report</span>
                <span className="text-emerald-400 font-bold font-mono">
                  Matched Trades: {benchmarkResult.matched_trades_count} | Discrepancies:{" "}
                  {benchmarkResult.discrepancy_count}
                </span>
              </div>

              {/* Delta Summary KPI */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
                <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">NET P&L DELTA</div>
                  <div className={`text-base font-bold mt-1 tabular-nums ${getPnlColor(benchmarkResult.pnl_diff)}`}>
                    {formatINR(benchmarkResult.pnl_diff)}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-1">Variance against StockMock</div>
                </div>
                <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">BENCHMARK FIDELITY</div>
                  <div className="text-base font-bold text-emerald-400 mt-1">
                    {benchmarkResult.is_matched ? "100% IDENTICAL" : "DIVERGENCES FOUND"}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-1">Execution logic parity</div>
                </div>
                <div className="p-3 rounded bg-[#090d14] border border-[#1a2336]">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">SUMMARY</div>
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
