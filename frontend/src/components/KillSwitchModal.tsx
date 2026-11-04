"use client";

import React, { useState } from "react";
import { AlertOctagon, X, ShieldAlert, CheckCircle2 } from "lucide-react";
import { api } from "../lib/api";

interface KillSwitchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function KillSwitchModal({ isOpen, onClose, onSuccess }: KillSwitchModalProps) {
  const [confirmationText, setConfirmationText] = useState("");
  const [reason, setReason] = useState("Operator manual emergency shutdown");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const isConfirmed = confirmationText.trim() === "CONFIRM";

  const handleExecute = async () => {
    if (!isConfirmed || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const res = await api.triggerKillSwitch(reason);
      setResult(
        `Kill-switch engaged! Session terminated. Active orders cancelled: ${res.open_orders_cancelled}, Positions closed: ${res.positions_closed}.`
      );
      if (onSuccess) onSuccess();
    } catch (err: any) {
      setError(err?.message || "Failed to trigger emergency kill-switch");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    setConfirmationText("");
    setResult(null);
    setError(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-lg border border-rose-600/60 bg-[#090d14] shadow-2xl shadow-rose-950/60 overflow-hidden font-mono text-xs">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-rose-900/40 bg-rose-950/30 px-5 py-3.5">
          <div className="flex items-center space-x-2 text-rose-400 font-bold text-sm tracking-wide">
            <AlertOctagon className="w-5 h-5 text-rose-500 animate-pulse" />
            <span>EMERGENCY KILL-SWITCH PROTOCOL</span>
          </div>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-slate-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-4">
          <div className="rounded border border-rose-500/30 bg-rose-950/20 p-3.5 text-xs text-rose-300 leading-relaxed">
            <strong>WARNING:</strong> This action immediately terminates the active trading session,
            cancels all open/pending orders at the OMS and Broker, flattens all open derivative
            positions at market, and locks strategy execution into an emergency locked-out state.
          </div>

          {result ? (
            <div className="rounded border border-emerald-500/30 bg-emerald-950/20 p-4 text-xs text-emerald-300 space-y-2">
              <div className="flex items-center space-x-2 font-bold text-emerald-400">
                <CheckCircle2 className="w-4 h-4" />
                <span>PROTOCOL EXECUTED SUCCESSFULLY</span>
              </div>
              <p>{result}</p>
              <div className="pt-2">
                <button
                  onClick={handleClose}
                  className="w-full py-2 btn-secondary text-xs"
                >
                  Dismiss
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="space-y-1.5">
                <label className="text-slate-300 font-medium">Kill-Switch Reason</label>
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="input-field text-slate-200"
                />
              </div>

              <div className="space-y-1.5 pt-1">
                <label className="text-slate-300">
                  Type <span className="font-bold text-rose-400">CONFIRM</span> to authenticate
                  emergency liquidation:
                </label>
                <input
                  type="text"
                  placeholder="Type CONFIRM"
                  value={confirmationText}
                  onChange={(e) => setConfirmationText(e.target.value)}
                  className="input-field text-slate-100 placeholder:text-slate-600 focus:border-rose-500"
                />
              </div>

              {error && (
                <div className="rounded border border-rose-500/40 bg-rose-950/40 p-2.5 text-xs text-rose-300">
                  {error}
                </div>
              )}

              <div className="flex items-center justify-end space-x-3 pt-3 border-t border-[#1a2336]">
                <button
                  type="button"
                  onClick={handleClose}
                  className="btn-secondary text-xs"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleExecute}
                  disabled={!isConfirmed || isSubmitting}
                  className={`flex items-center space-x-1.5 px-4 py-2 text-xs font-bold rounded transition-all ${
                    isConfirmed && !isSubmitting
                      ? "btn-danger shadow-lg shadow-rose-950/60"
                      : "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
                  }`}
                >
                  <ShieldAlert className="w-3.5 h-3.5" />
                  <span>{isSubmitting ? "ENGAGING..." : "ENGAGE KILL-SWITCH"}</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
