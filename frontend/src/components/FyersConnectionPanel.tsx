"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Radio, KeyRound, PlugZap, Square, RefreshCw, LogOut } from "lucide-react";
import { api } from "../lib/api";

type Stage = { label: string; ok: boolean; detail?: string };

function Badge({ stage }: { stage: Stage }) {
  return (
    <div
      className={`flex items-center justify-between px-2.5 py-1.5 rounded border text-[11px] font-mono ${
        stage.ok
          ? "bg-emerald-950/40 border-emerald-800/60 text-emerald-300"
          : "bg-[#090d14] border-[#1a2336] text-slate-400"
      }`}
      title={stage.detail}
    >
      <span>{stage.label}</span>
      <span className="font-bold">{stage.ok ? "YES" : "NO"}</span>
    </div>
  );
}

/**
 * FYERS connection panel. Shows four independent stages (authentication, WebSocket,
 * subscription, fresh ticks) and drives the OAuth flow. Never displays tokens: the
 * authorization code / redirect URL is entered into a masked field and sent once.
 */
export default function FyersConnectionPanel() {
  const [live, setLive] = useState<any>(null);
  const [codeInput, setCodeInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    api.getLiveFeedStatus().then(setLive).catch(() => setLive(null));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNotice(ok);
    } catch (e: any) {
      setNotice(String(e?.message || e).slice(0, 300));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const openLogin = () =>
    run(async () => {
      const r = await api.getFyersLoginUrl();
      window.open(r.login_url, "_blank", "noopener,noreferrer");
    }, "FYERS login opened in a new tab. After login, paste the full redirected URL below.");

  const exchange = () =>
    run(async () => {
      const r = await api.exchangeFyersCode(codeInput.trim());
      setCodeInput("");
      if (!r.authenticated) throw new Error(`Token stored but FYERS rejected it: ${r.reason}`);
    }, "Authenticated: FYERS accepted the token.");

  const auth = live?.authentication;
  const stages: Stage[] = [
    { label: "1. Authenticated (verified by FYERS API call)", ok: !!auth?.authenticated, detail: auth?.reason },
    { label: "2. WebSocket connected", ok: !!live?.websocket_connected },
    { label: "3. NIFTY50-INDEX subscribed", ok: !!live?.subscribed },
    { label: "4. Fresh exchange ticks flowing", ok: !!live?.fresh_tick_received },
  ];

  return (
    <div className="panel p-4 space-y-4 font-mono text-xs">
      <div className="panel-header">
        <div className="flex items-center space-x-2 font-bold text-slate-200">
          <Radio className="w-4 h-4 text-cyan-400" />
          <span>FYERS Live Market Data (PAPER execution only)</span>
        </div>
        <span
          className={`text-[10px] font-bold px-2 py-0.5 rounded border ${
            live?.live_data_connected
              ? "text-emerald-300 border-emerald-800 bg-emerald-950/50"
              : "text-amber-300 border-amber-800/70 bg-amber-950/30"
          }`}
        >
          {live?.live_data_connected ? "LIVE DATA" : `NOT LIVE (${live?.state ?? "NOT_STARTED"})`}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {stages.map((s) => (
          <Badge key={s.label} stage={s} />
        ))}
      </div>

      <div className="text-slate-500 text-[11px]">
        Auth status: <span className="text-slate-300">{auth?.reason ?? "unknown"}</span>
        {auth?.validated_at ? <> · verified {auth.validated_at}</> : null}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <button disabled={busy} onClick={openLogin} className="px-3 py-1.5 rounded bg-cyan-950/60 border border-cyan-800 text-cyan-200 hover:bg-cyan-900/60 flex items-center space-x-1.5">
            <KeyRound className="w-3.5 h-3.5" /><span>1. Open FYERS login</span>
          </button>
          <button disabled={busy} onClick={() => run(() => api.validateFyers(), "Validation complete.")} className="px-3 py-1.5 rounded bg-[#0d121c] border border-[#1c2638] text-slate-200 hover:border-slate-500 flex items-center space-x-1.5">
            <RefreshCw className="w-3.5 h-3.5" /><span>Verify session</span>
          </button>
        </div>
        <div className="flex gap-2">
          <input
            type="password"
            autoComplete="off"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            placeholder="2. Paste redirected URL or auth_code (masked)"
            className="flex-1 bg-[#090d14] border border-[#1a2336] rounded px-2 py-1.5 text-slate-200 placeholder-slate-600"
          />
          <button disabled={busy || !codeInput.trim()} onClick={exchange} className="px-3 py-1.5 rounded bg-emerald-950/60 border border-emerald-800 text-emerald-200 hover:bg-emerald-900/60 disabled:opacity-40">
            Exchange &amp; verify
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          <button disabled={busy || !auth?.authenticated || live?.pipeline_running} onClick={() => run(() => api.startLiveFeed(), "Live feed start requested.")} className="px-3 py-1.5 rounded bg-emerald-950/60 border border-emerald-800 text-emerald-200 disabled:opacity-40 flex items-center space-x-1.5">
            <PlugZap className="w-3.5 h-3.5" /><span>3. Start live feed</span>
          </button>
          <button disabled={busy || !live?.pipeline_running} onClick={() => run(() => api.stopLiveFeed(), "Live feed stopped.")} className="px-3 py-1.5 rounded bg-[#0d121c] border border-[#1c2638] text-slate-200 disabled:opacity-40 flex items-center space-x-1.5">
            <Square className="w-3.5 h-3.5" /><span>Stop</span>
          </button>
          <button disabled={busy} onClick={() => run(() => api.fyersLogout(), "Logged out; stored token removed.")} className="px-3 py-1.5 rounded bg-rose-950/50 border border-rose-900 text-rose-200 flex items-center space-x-1.5">
            <LogOut className="w-3.5 h-3.5" /><span>Logout</span>
          </button>
        </div>
      </div>

      {notice && <div className="text-[11px] text-amber-300 break-words">{notice}</div>}
      <p className="text-[10px] text-slate-500 leading-relaxed">
        A stored token never counts as connected. &quot;LIVE DATA&quot; appears only after FYERS confirms authentication, the
        WebSocket is open, the subscription is acknowledged, and a fresh exchange-timestamped tick has arrived.
      </p>
    </div>
  );
}

