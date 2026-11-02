"use client";

import React, { useEffect, useState } from "react";
import {
  Sliders,
  CheckCircle2,
  AlertTriangle,
  History,
  Save,
  Copy,
  GitCommit,
  GitCompare,
  ArrowRight,
} from "lucide-react";
import { api } from "../../lib/api";
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
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

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
    const timer = setTimeout(runValidation, 300);
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
      };

      if (selectedConfig) {
        const res = await api.createStrategyVersion(selectedConfig.config_id, {
          parameters: payload,
          set_active: true,
        });
        setSaveMessage(`Created Version ${res.version} successfully!`);
      } else {
        const res = await api.createStrategyConfig({
          underlying_symbol: underlying,
          template_type: "NIFTY_ORB",
          parameters: payload,
        });
        setSaveMessage(`Created configuration successfully! Version ${res.version}`);
      }
      await loadData();
    } catch (err: any) {
      setSaveMessage(`Error saving: ${err?.message}`);
    } finally {
      setIsSaving(false);
      setTimeout(() => setSaveMessage(null), 5000);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between border-b border-[#1c2638] pb-4">
        <div>
          <h1 className="text-lg font-bold font-mono text-slate-100 flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-cyan-400" />
            <span>STRATEGY BUILDER & VERSION CONTROL</span>
          </h1>
          <p className="text-xs font-mono text-slate-400 mt-1">
            Configure, validate, and version deterministic ORB rules against domain constraints.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <button
            onClick={handleSaveNewVersion}
            disabled={isSaving || (validation !== null && !validation.is_valid)}
            className={`flex items-center space-x-1.5 px-4 py-2 rounded text-xs font-mono font-bold transition-colors ${validation?.is_valid
                ? "bg-cyan-600 hover:bg-cyan-500 text-white shadow-md shadow-cyan-900/40"
                : "bg-slate-800 text-slate-500 cursor-not-allowed"
              }`}
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? "SAVING..." : "SAVE AS NEW VERSION"}</span>
          </button>
        </div>
      </div>

      {saveMessage && (
        <div className="rounded border border-cyan-500/40 bg-cyan-950/40 p-3 text-xs font-mono text-cyan-200">
          {saveMessage}
        </div>
      )}

      {/* Main Grid: Config Form (Left 2 cols) + Version Diff & History (Right 1 col) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Form Column */}
        <div className="lg:col-span-2 space-y-6">
          {/* Section 1: Core Strategy Identifiers */}
          <div className="panel p-5 space-y-4">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2">
              1. Strategy Identification & Instrument
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1">
                <label className="text-slate-400">Strategy Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Underlying Index</label>
                <select
                  value={underlying}
                  onChange={(e) => setUnderlying(e.target.value)}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                >
                  <option value="NSE:NIFTY50-INDEX">NSE:NIFTY50-INDEX (NIFTY 50)</option>
                  <option value="NSE:NIFTYBANK-INDEX" disabled>
                    NSE:NIFTYBANK-INDEX (Phase 8)
                  </option>
                </select>
              </div>
            </div>
          </div>

          {/* Section 2: Opening Range & Breakout Semantics */}
          <div className="panel p-5 space-y-4">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2">
              2. Opening Range & Breakout Mechanics (StockMock Spec)
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1">
                <label className="text-slate-400">Opening Range Window</label>
                <div className="flex items-center space-x-2">
                  <input
                    type="time"
                    value={orbStart}
                    onChange={(e) => setOrbStart(e.target.value)}
                    className="w-1/2 rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  />
                  <span className="text-slate-500">to</span>
                  <input
                    type="time"
                    value={orbEnd}
                    onChange={(e) => setOrbEnd(e.target.value)}
                    className="w-1/2 rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Range Boundary Mode</label>
                <select
                  value={boundaryMode}
                  onChange={(e) => setBoundaryMode(e.target.value)}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                >
                  <option value="up_to_0944_close">
                    up_to_0944_close (Strict StockMock 09:16-09:44:59)
                  </option>
                  <option value="up_to_0945_close">up_to_0945_close (Includes 09:45 candle)</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Breakout Confirmation</label>
                <select
                  value={breakoutMode}
                  onChange={(e) => setBreakoutMode(e.target.value)}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                >
                  <option value="candle_close">Candle Close (Close strictly above ORB High)</option>
                  <option value="wick_touch">Wick Touch / Tick Cross</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Allowed Entry Window</label>
                <div className="flex items-center space-x-2">
                  <input
                    type="time"
                    value={entryStart}
                    onChange={(e) => setEntryStart(e.target.value)}
                    className="w-1/2 rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  />
                  <span className="text-slate-500">to</span>
                  <input
                    type="time"
                    value={entryEnd}
                    onChange={(e) => setEntryEnd(e.target.value)}
                    className="w-1/2 rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>
            </div>

            {/* Trading Days Selector */}
            <div className="space-y-1.5 pt-2">
              <label className="text-xs font-mono text-slate-400">
                Trading Schedule (StockMock benchmarked: Mon, Thu, Fri)
              </label>
              <div className="flex flex-wrap gap-2">
                {["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"].map((day) => {
                  const isSelected = tradingDays.includes(day);
                  return (
                    <button
                      key={day}
                      type="button"
                      onClick={() => handleDayToggle(day)}
                      className={`px-3 py-1.5 rounded text-xs font-mono font-semibold transition-colors ${isSelected
                          ? "bg-cyan-950 text-cyan-300 border border-cyan-700"
                          : "bg-[#121824] text-slate-500 border border-[#1f293d] hover:text-slate-300"
                        }`}
                    >
                      {day}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Section 3: Risk Controls & Profit Protection */}
          <div className="panel p-5 space-y-4">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2">
              3. Risk Management & Profit Floor Mechanics
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="space-y-1">
                <label className="text-slate-400">Profit Protection Activation (₹)</label>
                <input
                  type="number"
                  value={profitTrigger}
                  onChange={(e) => setProfitTrigger(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Minimum Locked Profit (₹)</label>
                <input
                  type="number"
                  value={minProfitLock}
                  onChange={(e) => setMinProfitLock(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Profit Trail Step (₹)</label>
                <input
                  type="number"
                  value={profitStep}
                  onChange={(e) => setProfitStep(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Lock Increment per Step (₹)</label>
                <input
                  type="number"
                  value={profitLockIncrement}
                  onChange={(e) => setProfitLockIncrement(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Daily Max Loss Circuit Breaker (₹)</label>
                <input
                  type="number"
                  value={maxLossLimit}
                  onChange={(e) => setMaxLossLimit(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400">Execution Lots</label>
                <input
                  type="number"
                  value={lots}
                  min={1}
                  max={20}
                  onChange={(e) => setLots(Number(e.target.value))}
                  className="w-full rounded bg-[#121824] border border-[#1f293d] px-3 py-2 text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Validation & Version Control Column */}
        <div className="space-y-6">
          {/* Live Validation Card */}
          <div className="panel p-5 space-y-3">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center justify-between">
              <span>Domain Rule Validation</span>
              {validation?.is_valid ? (
                <span className="text-[10px] text-emerald-400 flex items-center space-x-1">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>VALID</span>
                </span>
              ) : (
                <span className="text-[10px] text-rose-400 flex items-center space-x-1">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>INVALID</span>
                </span>
              )}
            </div>

            {validation?.errors && validation.errors.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-[10px] font-mono text-rose-400 font-bold uppercase">Errors:</span>
                {validation.errors.map((err, i) => (
                  <div
                    key={i}
                    className="p-2 rounded bg-rose-950/40 border border-rose-800/40 text-[11px] font-mono text-rose-300"
                  >
                    • {err}
                  </div>
                ))}
              </div>
            )}

            {validation?.unresolved_decisions && validation.unresolved_decisions.length > 0 && (
              <div className="space-y-1.5">
                <span className="text-[10px] font-mono text-amber-400 font-bold uppercase">
                  Advisories & Disclosures:
                </span>
                {validation.unresolved_decisions.map((warn, i) => (
                  <div
                    key={i}
                    className="p-2 rounded bg-amber-950/30 border border-amber-800/30 text-[11px] font-mono text-amber-300"
                  >
                    • {warn}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Version History & Parameter Diff Viewer */}
          <div className="panel p-5 space-y-4">
            <div className="font-mono text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-[#1c2638] pb-2 flex items-center space-x-2">
              <History className="w-4 h-4 text-cyan-400" />
              <span>Version History ({configs.length})</span>
            </div>

            <div className="space-y-2 max-h-48 overflow-y-auto scrollbar-thin">
              {configs.map((cfg) => {
                const isSelected = selectedConfig?.config_id === cfg.config_id;
                return (
                  <div
                    key={cfg.config_id}
                    onClick={() => {
                      setSelectedConfig(cfg);
                      setUnderlying(cfg.underlying_symbol);
                    }}
                    className={`p-2.5 rounded border text-xs font-mono cursor-pointer transition-colors ${isSelected
                        ? "bg-[#162030] border-cyan-500/50 text-cyan-300"
                        : "bg-[#121824] border-[#1f293d] text-slate-400 hover:text-slate-200"
                      }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold">v{cfg.version} — {cfg.underlying_symbol}</span>
                      <span className="text-[10px] text-slate-500">{cfg.config_id.slice(0, 8)}</span>
                    </div>
                    <div className="text-[10px] text-slate-500 mt-1 flex justify-between">
                      <span>Status: {cfg.is_active ? "Active" : "Archived"}</span>
                      <span>Lots: {cfg.parameters?.lots || 1}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Parameter Diff Section */}
            <div className="pt-3 border-t border-[#1c2638] space-y-2">
              <div className="flex items-center space-x-1.5 text-xs font-mono font-bold text-slate-300">
                <GitCompare className="w-3.5 h-3.5 text-cyan-400" />
                <span>Active Diff: Current vs v1</span>
              </div>
              <div className="p-2.5 rounded bg-[#0e131d] border border-[#1c2638] font-mono text-[11px] space-y-1.5">
                <div className="flex justify-between text-slate-400">
                  <span>Parameter</span>
                  <span>Form vs Saved</span>
                </div>
                <div className="flex justify-between text-slate-300">
                  <span>Profit Trigger</span>
                  <span>₹{profitTrigger} vs ₹3,000</span>
                </div>
                <div className="flex justify-between text-slate-300">
                  <span>Max Daily Loss</span>
                  <span>₹{maxLossLimit} vs ₹3,000</span>
                </div>
                <div className="flex justify-between text-slate-300">
                  <span>Boundary Mode</span>
                  <span className="text-cyan-400">{boundaryMode}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}