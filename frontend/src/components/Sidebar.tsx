"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Sliders,
  TrendingUp,
  FlaskConical,
  Terminal,
  ListOrdered,
  ShieldAlert,
  Settings,
  Server,
  Activity,
  Layers,
  ChevronRight,
} from "lucide-react";

interface NavItem {
  name: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
  badgeType?: "live" | "sim" | "warn";
}

interface NavSection {
  title: string;
  items: NavItem[];
}

const NAV_SECTIONS: NavSection[] = [
  {
    title: "TRADING & EXECUTION",
    items: [
      { name: "Live Terminal", href: "/dashboard", icon: LayoutDashboard },
      { name: "Paper Simulator", href: "/paper", icon: Terminal, badge: "PAPER", badgeType: "sim" },
      { name: "Order Blotter", href: "/orders", icon: ListOrdered },
      { name: "Market Charts", href: "/charts", icon: TrendingUp },
    ],
  },
  {
    title: "STRATEGY & QUANT LAB",
    items: [
      { name: "Strategy Studio", href: "/strategies", icon: Sliders },
      { name: "Backtesting Lab", href: "/backtest", icon: FlaskConical },
    ],
  },
  {
    title: "SURVEILLANCE & SYSTEM",
    items: [
      { name: "Risk & Audit Control", href: "/risk", icon: ShieldAlert },
      { name: "System Settings", href: "/settings", icon: Settings },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 flex-shrink-0 border-r border-[#182133] bg-[#090d14] flex flex-col justify-between select-none">
      {/* Navigation Sections */}
      <div className="py-3 px-2 space-y-4">
        {NAV_SECTIONS.map((section, sIdx) => (
          <div key={sIdx} className="space-y-1">
            <div className="px-3 py-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-400">
              {section.title}
            </div>
            {section.items.map((item) => {
              const isActive =
                pathname === item.href ||
                (item.href !== "/dashboard" && pathname.startsWith(item.href)) ||
                (item.href === "/dashboard" && pathname === "/");
              const Icon = item.icon;

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`group flex items-center justify-between px-3 py-2 rounded text-xs font-mono transition-all ${
                    isActive
                      ? "bg-[#111927] text-cyan-400 font-semibold border-l-2 border-cyan-400 shadow-sm shadow-black/20"
                      : "text-slate-400 hover:bg-[#0e1420] hover:text-slate-200"
                  }`}
                >
                  <div className="flex items-center space-x-2.5">
                    <Icon
                      className={`w-4 h-4 transition-colors ${
                        isActive ? "text-cyan-400" : "text-slate-400 group-hover:text-slate-300"
                      }`}
                    />
                    <span>{item.name}</span>
                  </div>
                  {item.badge ? (
                    <span
                      className={`text-[9px] px-1.5 py-0.2 rounded font-bold tracking-wider ${
                        item.badgeType === "live"
                          ? "bg-cyan-950 text-cyan-300 border border-cyan-800"
                          : "bg-emerald-950/80 text-emerald-300 border border-emerald-800/60"
                      }`}
                    >
                      {item.badge}
                    </span>
                  ) : isActive ? (
                    <ChevronRight className="w-3 h-3 text-cyan-400 opacity-70" />
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </div>

      {/* Institutional Telemetry Footer Drawer */}
      <div className="p-3 border-t border-[#182133] bg-[#070a10] text-[11px] font-mono space-y-2">
        <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
          <span>SURVEILLANCE HUD</span>
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
        </div>

        <div className="flex items-center justify-between text-slate-400">
          <span>ACTIVE STRATEGY</span>
          <span className="text-slate-200 font-medium">NIFTY ORB v1.0</span>
        </div>

        <div className="flex items-center justify-between text-slate-400">
          <span>DAILY LOSS LIMIT</span>
          <span className="text-rose-400 font-medium tabular-nums">₹3,000.00</span>
        </div>

        <div className="flex items-center justify-between text-slate-400">
          <span>PROFIT TRAILING</span>
          <span className="text-emerald-400 font-medium tabular-nums">+₹500 / 1K</span>
        </div>

        <div className="pt-2 border-t border-[#141b2b] flex items-center justify-between text-slate-400 text-[10px]">
          <div className="flex items-center space-x-1.5">
            <Server className="w-3 h-3 text-slate-400" />
            <span>DB CONNECTED</span>
          </div>
          <span className="text-emerald-400 font-bold">OK</span>
        </div>
      </div>
    </aside>
  );
}
