"use client";

import React, { useEffect, useState } from "react";
import {
  Sliders,
  CheckCircle2,
  AlertTriangle,
  History,
  Save,
  GitCompare,
  ArrowRight,
  ShieldAlert,
  Clock,
  Layers,
  Code,
  Calendar,
  Check,
  TrendingUp,
  Lock,
} from "lucide-react";
import { api } from "../../lib/api";
import { formatINR, formatNumber } from "../../lib/formatters";
import {
  StrategyConfigVersionResponse,
  StrategyValidateResponse,
  StrategyTemplateResponse,
} from "../../types/api";

export default function StrategiesPage() {
  const [templates, setTemplates] = useState<StrategyTemplateResponse[]>([]);
  const [configs, setConfigs] = useState<StrategyConfigVersionResponse[]>([]);
  const [selectedConfig, setSelectedConfig] = useState<StrategyConfigVersionResponse | null>(null);

  // Form parameters
  const [name, setName] = useState("NIFTY ORB Production Strategy");
  const [underlying, setUnderlying] = useState("NSE:NIFTY50-INDEX");
  const [orbStart, setOrbStart] = useState("09:16");
  const [orbEnd, setOrbEnd] = useState("09:45");
  const [breakoutMode, setBreakoutMode] = useState("candle_close");
  const [boundaryMode, setBoundaryMode] = useState("up_to_0944_close");
  const [entryStart, setEntryStart] = useState("09:45");
  const [entryEnd, setEntryEnd] = useState("15:15");
  const [tradingDays, setTradingDays] = useState(["MONDAY", "THURSDAY", "FRIDAY"]);
  const [lots, setLots] = useState(1);
  const [strikeSelection, setStrikeSelection] = useState("ATM");
  const [profitTrigger, setProfitTrigger] = useState(3000);
  const [minProfitLock, setMinProfitLock] = useState(500);
  const [profitStep, setProfitStep] = useState(1000);
  const [profitLockIncrement, setProfitLockIncrement] = useState(500);
  const [maxLossLimit, setMaxLossLimit] = useState(3000);

  const [validation, setValidation] = useState<StrategyValidateResponse | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ text: string; type: "success" | "error" } | null>(null);
  const [showJsonPreview, setShowJsonPreview] = useState(false);

  // Fetch initial data
  const loadData = async () => {
    try {
      const [tRes, cRes] = await Promise.all([
        api.getStrategyTemplates(),
        api.getStrategyConfigs(),
      ]);
      setTemplates(Array.isArray(tRes) ? tRes : []);
      const cfgs = Array.isArray(cRes) ? cRes : [];
      setConfigs(cfgs);
      if (cfgs.length > 0) {
        const primary = cfgs[0];
        setSelectedConfig(primary);
        setUnderlying(primary.underlying_symbol);
        if (primary.parameters) {
          if (primary.parameters.strategy_name) setName(primary.parameters.strategy_name);
          if (primary.parameters.opening_range_start) setOrbStart(primary.parameters.opening_range_start);
          if (primary.parameters.opening_range_end) setOrbEnd(primary.parameters.opening_range_end);
          if (primary.parameters.breakout_confirmation) setBreakoutMode(primary.parameters.breakout_confirmation);
          if (primary.parameters.range_boundary_mode) setBoundaryMode(primary.parameters.range_boundary_mode);
          if (primary.parameters.entry_start_time) setEntryStart(primary.parameters.entry_start_time);
          if (primary.parameters.entry_end_time) setEntryEnd(primary.parameters.entry_end_time);
          if (Array.isArray(primary.parameters.trading_days)) setTradingDays(primary.parameters.trading_days);
          if (primary.parameters.lots) setLots(Number(primary.parameters.lots));
          if (primary.parameters.strike_selection_mode) setStrikeSelection(primary.parameters.strike_selection_mode);
          if (primary.parameters.profit_protection_trigger) setProfitTrigger(Number(primary.parameters.profit_protection_trigger));
          if (primary.parameters.minimum_profit_lock) setMinProfitLock(Number(primary.parameters.minimum_profit_lock));
          if (primary.parameters.profit_step) setProfitStep(Number(primary.parameters.profit_step));
          if (primary.parameters.profit_lock_increment) setProfitLockIncrement(Number(primary.parameters.profit_lock_increment));
          if (primary.parameters.max_daily_loss || primary.parameters.max_loss_limit) setMaxLossLimit(Number(primary.parameters.max_daily_loss || primary.parameters.max_loss_limit));
        }
      }
    } catch (err) {
      console.error("Failed to load strategy configs:", err);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Validate whenever parameters change
  useEffect(() => {
    const runValidation = async () => {
      try {
        const payload = {
          strategy_name: name,
          underlying_symbol: underlying,
          opening_range_start: orbStart,
          opening_range_end: orbEnd,
          breakout_confirmation: breakoutMode,
          range_boundary_mode: boundaryMode,
          entry_start_time: entryStart,
          entry_end_time: entryEnd,
          trading_days: tradingDays,
          lots: Number(lots),
          strike_selection_mode: strikeSelection,
          profit_protection_trigger: Number(profitTrigger),
          minimum_profit_lock: Number(minProfitLock),
          profit_step: Number(profitStep),
          profit_lock_increment: Number(profitLockIncrement),
          max_loss_limit: Number(maxLossLimit),
        max_daily_loss: Number(maxLossLimit),
        };
        const result = await api.validateStrategy({
          template_type: "NIFTY_ORB",
          parameters: payload,
        });
        setValidation(result);
      } catch (err) {
        console.error("Validation failed:", err);
      }
    };
    const timer = setTimeout(runValidation, 250);
    return () => clearTimeout(timer);
  }, [
    name,
    underlying,
    orbStart,
    orbEnd,
    breakoutMode,
    boundaryMode,
    entryStart,
    entryEnd,
    tradingDays,
    lots,
    strikeSelection,
    profitTrigger,
    minProfitLock,
    profitStep,
    profitLockIncrement,
    maxLossLimit,
  ]);

  const handleDayToggle = (day: string) => {
    if (tradingDays.includes(day)) {
      setTradingDays(tradingDays.filter((d) => d !== day));
    } else {
      setTradingDays([...tradingDays, day]);
    }
  };

  const handleSelectConfig = (cfg: StrategyConfigVersionResponse) => {
    setSelectedConfig(cfg);
    setUnderlying(cfg.underlying_symbol);
    if (cfg.parameters) {
      if (cfg.parameters.strategy_name) setName(cfg.parameters.strategy_name);
      if (cfg.parameters.opening_range_start) setOrbStart(cfg.parameters.opening_range_start);
      if (cfg.parameters.opening_range_end) setOrbEnd(cfg.parameters.opening_range_end);
      if (cfg.parameters.breakout_confirmation) setBreakoutMode(cfg.parameters.breakout_confirmation);
      if (cfg.parameters.range_boundary_mode) setBoundaryMode(cfg.parameters.range_boundary_mode);
      if (cfg.parameters.entry_start_time) setEntryStart(cfg.parameters.entry_start_time);
      if (cfg.parameters.entry_end_time) setEntryEnd(cfg.parameters.entry_end_time);
      if (Array.isArray(cfg.parameters.trading_days)) setTradingDays(cfg.parameters.trading_days);
      if (cfg.parameters.lots) setLots(Number(cfg.parameters.lots));
      if (cfg.parameters.strike_selection_mode) setStrikeSelection(cfg.parameters.strike_selection_mode);
      if (cfg.parameters.profit_protection_trigger) setProfitTrigger(Number(cfg.parameters.profit_protection_trigger));
      if (cfg.parameters.minimum_profit_lock) setMinProfitLock(Number(cfg.parameters.minimum_profit_lock));
      if (cfg.parameters.profit_step) setProfitStep(Number(cfg.parameters.profit_step));
      if (cfg.parameters.profit_lock_increment) setProfitLockIncrement(Number(cfg.parameters.profit_lock_increment));
      if (cfg.parameters.max_daily_loss || cfg.parameters.max_loss_limit) setMaxLossLimit(Number(cfg.parameters.max_daily_loss || cfg.parameters.max_loss_limit));
    }
  };

  const handleSaveNewVersion = async () => {
    setIsSaving(true);
    setSaveMessage(null);
    try {
      const payload = {
        strategy_name: name,
        underlying_symbol: underlying,
        opening_range_start: orbStart,
        opening_range_end: orbEnd,
        breakout_confirmation: breakoutMode,
        range_boundary_mode: boundaryMode,
        entry_start_time: entryStart,
        entry_end_time: entryEnd,
        trading_days: tradingDays,
        lots: Number(lots),
        strike_selection_mode: strikeSelection,
        profit_protection_trigger: Number(profitTrigger),
        minimum_profit_lock: Number(minProfitLock),
        profit_step: Number(profitStep),
        profit_lock_increment: Number(profitLockIncrement),
        max_loss_limit: Number(maxLossLimit),
        max_daily_loss: Number(maxLossLimit),
      };

      if (selectedConfig) {
        const res = await api.createStrategyVersion(selectedConfig.config_id, {
          parameters: payload,
          set_active: true,
        });
        setSaveMessage({ text: `Published Version ${res.version} successfully! Active in production.`, type: "success" });
      } else {
        const res = await api.createStrategyConfig({
          underlying_symbol: underlying,
          template_type: "NIFTY_ORB",
          parameters: payload,
        });
        setSaveMessage({ text: `Created configuration successfully! Active Version ${res.version}`, type: "success" });
      }
      await loadData();
    } catch (err: any) {
      setSaveMessage({ text: `Error saving: ${err?.message}`, type: "error" });
    } finally {
      setIsSaving(false);
      setTimeout(() => setSaveMessage(null), 5000);
    }
  };

  const handleActivateConfig = async (configId: string) => {
    try {
      await api.activateStrategyConfig(configId);
      setSaveMessage({ text: `Activated configuration ${configId} successfully!`, type: "success" });
      await loadData();
    } catch (err: any) {
      setSaveMessage({ text: `Error activating: ${err?.message}`, type: "error" });
    }
  };

  const currentPayloadJson = {
    strategy_name: name,
    underlying_symbol: underlying,
    opening_range_start: orbStart,
    opening_range_end: orbEnd,
    breakout_confirmation: breakoutMode,
    range_boundary_mode: boundaryMode,
    entry_start_time: entryStart,
    entry_end_time: entryEnd,
    trading_days: tradingDays,
    lots: Number(lots),
    strike_selection_mode: strikeSelection,
    profit_protection_trigger: Number(profitTrigger),
    minimum_profit_lock: Number(minProfitLock),
    profit_step: Number(profitStep),
    profit_lock_increment: Number(profitLockIncrement),
    max_loss_limit: Number(maxLossLimit),
  };

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#1a2336] pb-4 gap-3">
        <div>
          <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
            <span>QUANT LAB</span>
            <span>/</span>
            <span>STRATEGY STUDIO</span>
            <span>/</span>
            <span className="text-cyan-400">ORB RULE ENGINE</span>
          </div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-cyan-400" />
            <span>NIFTY ORB STRATEGY BUILDER & VERSIONING</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            Deterministic StockMock-benchmarked parameters, domain invariants, and immutable version control.
          </p>
        </div>

        <div className="flex items-center space-x-3">
          {selectedConfig && (
            <div className="hidden sm:flex items-center space-x-2 px-3 py-1.5 rounded bg-[#0d121c] border border-[#1a2336] text-xs font-mono">
              <span className="text-slate-400">Active Baseline:</span>
              <span className="font-bold text-cyan-300">v{selectedConfig.version}</span>
              <span className="text-slate-600">|</span>
              <span className="text-[10px] text-emerald-400 font-semibold uppercase">{selectedConfig.is_active ? "LIVE ACTIVE" : "ARCHIVED"}</span>
            </div>
          )}

          <button
            onClick={() => setShowJsonPreview(!showJsonPreview)}
            className="btn-secondary text-xs flex items-center space-x-1.5"
            title="Inspect Raw JSON Configuration"
          >
            <Code className="w-3.5 h-3.5 text-slate-400" />
            <span>{showJsonPreview ? "Hide JSON" : "Raw JSON"}</span>
          </button>

          <button
            onClick={handleSaveNewVersion}
            disabled={isSaving || (validation !== null && !validation.is_valid)}
            className={`flex items-center space-x-1.5 px-4 py-2 rounded text-xs font-mono font-bold transition-all ${
              validation?.is_valid
                ? "btn-primary shadow-lg shadow-cyan-950/50"
                : "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
            }`}
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? "COMPILING & SAVING..." : "PUBLISH AS NEW VERSION"}</span>
          </button>
        </div>
      </div>

      {/* Save Status Banner */}
      {saveMessage && (
        <div
          className={`rounded border p-3 text-xs font-mono flex items-center space-x-2 ${
            saveMessage.type === "success"
              ? "border-emerald-500/40 bg-emerald-950/40 text-emerald-200"
              : "border-rose-500/40 bg-rose-950/40 text-rose-200"
          }`}
        >
          {saveMessage.type === "success" ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          )}
          <span>{saveMessage.text}</span>
        </div>
      )}

      {/* Strategy Rule Progression Stepper */}
      <div className="panel p-3.5">
        <div className="flex items-center justify-between mb-2 pb-2 border-b border-[#1a2336] text-[10px] font-mono uppercase text-slate-400 font-bold tracking-wider">
          <div className="flex items-center space-x-2">
            <Layers className="w-3.5 h-3.5 text-cyan-400" />
            <span>5-PHASE ORB RULE STATE MACHINE</span>
          </div>
          <span className="text-slate-500">Deterministic StockMock Semantics</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-2 text-xs font-mono">
          <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] flex flex-col justify-between">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-bold text-cyan-400">PHASE 01</span>
              <span>09:16-09:44</span>
            </div>
            <div className="font-bold text-slate-200 mt-1 text-[11px]">Range Formation</div>
            <div className="text-[10px] text-slate-500 mt-0.5">High/Low established from 1m candles</div>
          </div>
          <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] flex flex-col justify-between">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-bold text-cyan-400">PHASE 02</span>
              <span>09:45:00</span>
            </div>
            <div className="font-bold text-slate-200 mt-1 text-[11px]">Boundary Lock</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Range boundaries locked for day</div>
          </div>
          <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] flex flex-col justify-between">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-bold text-cyan-400">PHASE 03</span>
              <span>09:45-15:15</span>
            </div>
            <div className="font-bold text-slate-200 mt-1 text-[11px]">Breakout Trigger</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Candle close breach triggers ATM order</div>
          </div>
          <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] flex flex-col justify-between">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-bold text-emerald-400">PHASE 04</span>
              <span>Dynamic</span>
            </div>
            <div className="font-bold text-slate-200 mt-1 text-[11px]">Stepped Trailing</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Floor locks at ₹3k, steps +₹500 / ₹1k</div>
          </div>
          <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] flex flex-col justify-between">
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span className="font-bold text-rose-400">PHASE 05</span>
              <span>15:10 / Breaker</span>
            </div>
            <div className="font-bold text-slate-200 mt-1 text-[11px]">Auto Square-Off</div>
            <div className="text-[10px] text-slate-500 mt-0.5">EOD liquidation or ₹3k max loss stop</div>
          </div>
        </div>
      </div>

      {/* Main Grid: Form Column (Left 2 cols) + Validation & Diff Column (Right 1 col) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left Form Modules */}
        <div className="lg:col-span-2 space-y-5">
          {/* Section 1: Identification & Instrument */}
          <div className="panel p-4 space-y-4">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
                <span>1. Strategy Identification & Contract Specifications</span>
              </div>
              <span className="text-[10px] font-normal text-slate-500">Fixed-Contract Derivatives</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Strategy Label</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input-field text-slate-100"
                  placeholder="e.g. NIFTY ORB Production Strategy"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Underlying Asset</label>
                <select
                  value={underlying}
                  onChange={(e) => setUnderlying(e.target.value)}
                  className="select-field text-slate-100"
                >
                  <option value="NSE:NIFTY50-INDEX">NSE:NIFTY50-INDEX (NIFTY 50 Benchmark)</option>
                  <option value="NSE:NIFTYBANK-INDEX" disabled>
                    NSE:NIFTYBANK-INDEX (Bank Nifty - Reserved)
                  </option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Strike Selection Mode</label>
                <select
                  value={strikeSelection}
                  onChange={(e) => setStrikeSelection(e.target.value)}
                  className="select-field text-slate-100"
                >
                  <option value="ATM">ATM (Nearest Round Strike to Breakout Spot)</option>
                  <option value="ITM1" disabled>ITM 1 (Phase 10)</option>
                  <option value="OTM1" disabled>OTM 1 (Phase 10)</option>
                </select>
                <div className="text-[10px] text-slate-500">Calls closest 50-point strike (e.g. 25,000 CE/PE)</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Execution Quantity (Lots)</label>
                <div className="flex items-center space-x-2">
                  <input
                    type="number"
                    value={lots}
                    min={1}
                    max={20}
                    onChange={(e) => setLots(Math.max(1, Number(e.target.value)))}
                    className="input-field text-slate-100 tabular-nums"
                  />
                  <div className="px-3 py-2 rounded bg-[#090d14] border border-[#1a2336] text-[11px] text-slate-400 whitespace-nowrap">
                    Qty: <strong className="text-slate-200 tabular-nums">{lots * 25}</strong> (25/lot)
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Timing, Boundaries & Breakout Semantics */}
          <div className="panel p-4 space-y-4">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
                <span>2. Opening Range & Breakout Semantics (StockMock Spec)</span>
              </div>
              <span className="text-[10px] font-normal text-slate-500">Deterministic Clock</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Opening Range Formation Window</label>
                <div className="flex items-center space-x-2">
                  <input
                    type="time"
                    value={orbStart}
                    onChange={(e) => setOrbStart(e.target.value)}
                    className="input-field text-slate-100"
                  />
                  <span className="text-slate-500 font-bold">to</span>
                  <input
                    type="time"
                    value={orbEnd}
                    onChange={(e) => setOrbEnd(e.target.value)}
                    className="input-field text-slate-100"
                  />
                </div>
                <div className="text-[10px] text-slate-500">Benchmark window: 09:16 to 09:45 IST</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Range Boundary Inclusion Mode</label>
                <select
                  value={boundaryMode}
                  onChange={(e) => setBoundaryMode(e.target.value)}
                  className="select-field text-slate-100"
                >
                  <option value="up_to_0944_close">
                    up_to_0944_close (StockMock Spec: 09:16:00 to 09:44:59)
                  </option>
                  <option value="up_to_0945_close">
                    up_to_0945_close (Extended: Includes 09:45:00 candle)
                  </option>
                </select>
                <div className="text-[10px] text-slate-500">StockMock locks high/low before the 09:45 candle closes</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Breakout Confirmation Method</label>
                <select
                  value={breakoutMode}
                  onChange={(e) => setBreakoutMode(e.target.value)}
                  className="select-field text-slate-100"
                >
                  <option value="candle_close">Candle Close (Close strictly above High / below Low)</option>
                  <option value="wick_touch">Wick Touch / Tick Cross (Intrabar breach)</option>
                </select>
                <div className="text-[10px] text-slate-500">Filters false breakouts and whip-saws</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Allowed Breakout Entry Window</label>
                <div className="flex items-center space-x-2">
                  <input
                    type="time"
                    value={entryStart}
                    onChange={(e) => setEntryStart(e.target.value)}
                    className="input-field text-slate-100"
                  />
                  <span className="text-slate-500 font-bold">to</span>
                  <input
                    type="time"
                    value={entryEnd}
                    onChange={(e) => setEntryEnd(e.target.value)}
                    className="input-field text-slate-100"
                  />
                </div>
                <div className="text-[10px] text-slate-500">No new orders accepted after 15:15 IST</div>
              </div>
            </div>

            {/* Trading Schedule Selector */}
            <div className="pt-2 border-t border-[#1a2336] space-y-2">
              <div className="flex items-center justify-between text-xs font-mono">
                <label className="text-slate-300 font-semibold flex items-center space-x-1.5">
                  <Calendar className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Authorized Trading Schedule (StockMock Benchmark)</span>
                </label>
                <span className="text-[10px] text-slate-500">Benchmark: Monday, Thursday, Friday</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"].map((day) => {
                  const isSelected = tradingDays.includes(day);
                  const isBenchmarkDay = ["MONDAY", "THURSDAY", "FRIDAY"].includes(day);
                  return (
                    <button
                      key={day}
                      type="button"
                      onClick={() => handleDayToggle(day)}
                      className={`px-3 py-1.5 rounded text-xs font-mono font-bold transition-all flex items-center space-x-1.5 ${
                        isSelected
                          ? "bg-cyan-950/70 text-cyan-300 border border-cyan-600/70 shadow-sm shadow-cyan-950/50"
                          : "bg-[#090d14] text-slate-500 border border-[#1a2336] hover:text-slate-300 hover:border-slate-700"
                      }`}
                    >
                      {isSelected && <Check className="w-3 h-3 text-cyan-400" />}
                      <span>{day}</span>
                      {isBenchmarkDay && (
                        <span className="text-[9px] px-1 rounded bg-cyan-900/40 text-cyan-400 font-normal">
                          ★
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Section 3: Risk Controls & Stepped Profit Trailing */}
          <div className="panel p-4 space-y-4">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
                <span>3. Capital Preservation & Stepped Profit Protection</span>
              </div>
              <span className="text-[10px] font-normal text-slate-500">Autonomous OMS Liquidation</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Profit Protection Activation Trigger (₹)</label>
                <div className="relative">
                  <input
                    type="number"
                    value={profitTrigger}
                    onChange={(e) => setProfitTrigger(Number(e.target.value))}
                    className="input-field text-slate-100 tabular-nums"
                  />
                </div>
                <div className="text-[10px] text-slate-500">First floor triggers when session MTM reaches this value</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Initial Minimum Locked Profit Floor (₹)</label>
                <input
                  type="number"
                  value={minProfitLock}
                  onChange={(e) => setMinProfitLock(Number(e.target.value))}
                  className="input-field text-slate-100 tabular-nums"
                />
                <div className="text-[10px] text-slate-500">Hard floor locked at activation (e.g. ₹500 locked at ₹3,000)</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Stepped Trailing MTM Step (₹)</label>
                <input
                  type="number"
                  value={profitStep}
                  onChange={(e) => setProfitStep(Number(e.target.value))}
                  className="input-field text-slate-100 tabular-nums"
                />
                <div className="text-[10px] text-slate-500">Floor raises every time MTM rises by this step amount</div>
              </div>

              <div className="space-y-1.5">
                <label className="text-slate-400 font-medium">Floor Lock Increment Per Step (₹)</label>
                <input
                  type="number"
                  value={profitLockIncrement}
                  onChange={(e) => setProfitLockIncrement(Number(e.target.value))}
                  className="input-field text-slate-100 tabular-nums"
                />
                <div className="text-[10px] text-slate-500">Amount added to locked floor per step (e.g. +₹500)</div>
              </div>

              <div className="space-y-1.5 md:col-span-2">
                <label className="text-slate-400 font-medium flex items-center justify-between">
                  <span className="text-rose-400 flex items-center space-x-1">
                    <ShieldAlert className="w-3.5 h-3.5" />
                    <span>Hard Daily Loss Circuit Breaker Limit (₹)</span>
                  </span>
                  <span className="text-[10px] text-slate-500">Non-negotiable OMS lockout</span>
                </label>
                <input
                  type="number"
                  value={maxLossLimit}
                  onChange={(e) => setMaxLossLimit(Number(e.target.value))}
                  className="input-field text-rose-300 font-bold tabular-nums border-rose-900/50 focus:border-rose-500"
                />
                <div className="text-[10px] text-rose-400/80">
                  If cumulative session MTM touches -₹{formatNumber(maxLossLimit, 2)}, all open positions are flattened at market and order entry is locked for the remainder of the trading day.
                </div>
              </div>
            </div>

            {/* Interactive Trailing Visualizer Box */}
            <div className="p-3 rounded bg-[#090d14] border border-[#1a2336] text-[11px] font-mono space-y-1.5 text-slate-300">
              <div className="text-[10px] uppercase text-slate-500 font-bold tracking-wider">
                Active Trailing Schedule
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1 text-[11px]">
                <div className="p-2 rounded bg-[#0d121c] border border-[#1a2336]">
                  <span className="text-slate-500 block text-[10px]">Tier 1 (Activation)</span>
                  <span className="text-slate-200">MTM ≥ ₹{formatNumber(profitTrigger, 0)}</span>
                  <span className="text-emerald-400 font-bold block mt-0.5">Floor: ₹{formatNumber(minProfitLock, 0)}</span>
                </div>
                <div className="p-2 rounded bg-[#0d121c] border border-[#1a2336]">
                  <span className="text-slate-500 block text-[10px]">Tier 2 (+1 Step)</span>
                  <span className="text-slate-200">MTM ≥ ₹{formatNumber(profitTrigger + profitStep, 0)}</span>
                  <span className="text-emerald-400 font-bold block mt-0.5">Floor: ₹{formatNumber(minProfitLock + profitLockIncrement, 0)}</span>
                </div>
                <div className="p-2 rounded bg-[#0d121c] border border-[#1a2336]">
                  <span className="text-slate-500 block text-[10px]">Tier 3 (+2 Steps)</span>
                  <span className="text-slate-200">MTM ≥ ₹{formatNumber(profitTrigger + profitStep * 2, 0)}</span>
                  <span className="text-emerald-400 font-bold block mt-0.5">Floor: ₹{formatNumber(minProfitLock + profitLockIncrement * 2, 0)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Validation & Versioning Column */}
        <div className="space-y-5">
          {/* Domain Validation HUD */}
          <div className="panel p-4 space-y-3">
            <div className="panel-header">
              <span>Domain Schema Invariants</span>
              {validation?.is_valid ? (
                <span className="badge-emerald flex items-center space-x-1">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>SCHEMA VALID</span>
                </span>
              ) : (
                <span className="badge-rose flex items-center space-x-1">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>INVARIANTS VIOLATED</span>
                </span>
              )}
            </div>

            {validation?.errors && validation.errors.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-[10px] font-mono text-rose-400 font-bold uppercase tracking-wider">
                  Validation Errors ({validation.errors.length}):
                </span>
                {validation.errors.map((err, i) => (
                  <div
                    key={i}
                    className="p-2 rounded bg-rose-950/30 border border-rose-800/40 text-[11px] font-mono text-rose-300 leading-tight"
                  >
                    • {err}
                  </div>
                ))}
              </div>
            )}

            {validation?.unresolved_decisions && validation.unresolved_decisions.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-[10px] font-mono text-amber-400 font-bold uppercase tracking-wider">
                  StockMock Invariant Advisories:
                </span>
                {validation.unresolved_decisions.map((warn, i) => (
                  <div
                    key={i}
                    className="p-2 rounded bg-amber-950/20 border border-amber-800/30 text-[11px] font-mono text-amber-300 leading-tight"
                  >
                    • {warn}
                  </div>
                ))}
              </div>
            )}

            {validation?.is_valid && (!validation.errors || validation.errors.length === 0) && (
              <div className="p-2.5 rounded bg-emerald-950/20 border border-emerald-900/40 text-[11px] font-mono text-emerald-300 flex items-center space-x-2">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                <span>All parameters satisfy deterministic execution requirements and StockMock constraints.</span>
              </div>
            )}
          </div>

          {/* Version History List */}
          <div className="panel p-4 space-y-3">
            <div className="panel-header">
              <div className="flex items-center space-x-2">
                <History className="w-3.5 h-3.5 text-cyan-400" />
                <span>Strategy Version Tree ({configs.length})</span>
              </div>
              <span className="text-[10px] text-slate-500">Immutable Records</span>
            </div>

            <div className="space-y-2 max-h-56 overflow-y-auto scrollbar-thin">
              {configs.map((cfg) => {
                const isSelected = selectedConfig?.config_id === cfg.config_id;
                return (
                  <div
                    key={cfg.config_id}
                    onClick={() => handleSelectConfig(cfg)}
                    className={`p-2.5 rounded border text-xs font-mono cursor-pointer transition-all ${
                      isSelected
                        ? "bg-[#0d1624] border-cyan-500/60 text-cyan-200 shadow-sm"
                        : "bg-[#090d14] border-[#1a2336] text-slate-400 hover:text-slate-200 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="font-bold text-slate-100">v{cfg.version}</span>
                        <span className="text-[10px] text-slate-400 font-medium">{cfg.underlying_symbol}</span>
                      </div>
                      {cfg.is_active ? (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">
                          ACTIVE
                        </span>
                      ) : (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleActivateConfig(cfg.config_id);
                          }}
                          className="px-2 py-0.5 rounded text-[9px] font-bold bg-[#142034] text-cyan-300 border border-cyan-500/40 hover:bg-cyan-900/40"
                          title="Set this version as active configuration"
                        >
                          ACTIVATE
                        </button>
                      )}
                    </div>
                    <div className="text-[10px] text-slate-500 mt-1 flex justify-between">
                      <span>ID: {cfg.config_id.slice(0, 8)}...</span>
                      <span>Lots: {cfg.parameters?.lots || 1} | Max Loss: ₹{cfg.parameters?.max_loss_limit || 3000}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Active Diff Comparison */}
          {selectedConfig && (
            <div className="panel p-4 space-y-3">
              <div className="panel-header">
                <div className="flex items-center space-x-1.5 text-xs font-mono font-bold text-slate-300">
                  <GitCompare className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Active Baseline Diff vs v{selectedConfig.version}</span>
                </div>
                <span className="text-[10px] text-slate-500">Runtime Deltas</span>
              </div>

              <div className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] font-mono text-[11px] space-y-2">
                <div className="grid grid-cols-3 text-slate-500 border-b border-[#1a2336] pb-1 text-[10px]">
                  <span>PARAMETER</span>
                  <span className="text-right">SAVED v{selectedConfig.version}</span>
                  <span className="text-right">DRAFT VALUE</span>
                </div>

                <div className="grid grid-cols-3 text-slate-300">
                  <span className="text-slate-400 truncate">Profit Trigger</span>
                  <span className="text-right tabular-nums text-slate-500">
                    ₹{selectedConfig.parameters?.profit_protection_trigger ?? 3000}
                  </span>
                  <span className={`text-right tabular-nums font-bold ${
                    profitTrigger !== (selectedConfig.parameters?.profit_protection_trigger ?? 3000)
                      ? "text-cyan-400"
                      : "text-slate-300"
                  }`}>
                    ₹{profitTrigger}
                  </span>
                </div>

                <div className="grid grid-cols-3 text-slate-300">
                  <span className="text-slate-400 truncate">Max Daily Loss</span>
                  <span className="text-right tabular-nums text-slate-500">
                    ₹{selectedConfig.parameters?.max_loss_limit ?? 3000}
                  </span>
                  <span className={`text-right tabular-nums font-bold ${
                    maxLossLimit !== (selectedConfig.parameters?.max_loss_limit ?? 3000)
                      ? "text-cyan-400"
                      : "text-slate-300"
                  }`}>
                    ₹{maxLossLimit}
                  </span>
                </div>

                <div className="grid grid-cols-3 text-slate-300">
                  <span className="text-slate-400 truncate">Lots</span>
                  <span className="text-right tabular-nums text-slate-500">
                    {selectedConfig.parameters?.lots ?? 1}
                  </span>
                  <span className={`text-right tabular-nums font-bold ${
                    lots !== (selectedConfig.parameters?.lots ?? 1)
                      ? "text-cyan-400"
                      : "text-slate-300"
                  }`}>
                    {lots}
                  </span>
                </div>

                <div className="grid grid-cols-3 text-slate-300">
                  <span className="text-slate-400 truncate">Boundary Mode</span>
                  <span className="text-right text-[10px] text-slate-500 truncate" title={selectedConfig.parameters?.range_boundary_mode}>
                    {selectedConfig.parameters?.range_boundary_mode === "up_to_0944_close" ? "0944 Close" : "0945 Close"}
                  </span>
                  <span className={`text-right text-[10px] font-bold truncate ${
                    boundaryMode !== selectedConfig.parameters?.range_boundary_mode
                      ? "text-cyan-400"
                      : "text-slate-300"
                  }`}>
                    {boundaryMode === "up_to_0944_close" ? "0944 Close" : "0945 Close"}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Raw JSON Preview Modal/Card */}
          {showJsonPreview && (
            <div className="panel p-4 space-y-2">
              <div className="panel-header">
                <span className="text-cyan-300 font-mono text-xs">Runtime Payload JSON</span>
                <span className="text-[10px] text-slate-500">FastAPI Ingest</span>
              </div>
              <pre className="p-2.5 rounded bg-[#090d14] border border-[#1a2336] text-[10px] font-mono text-slate-300 overflow-x-auto max-h-48 scrollbar-thin">
                {JSON.stringify(currentPayloadJson, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
