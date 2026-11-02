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
  ShieldCheck,
  Server,
} from "lucide-react";

interface NavItem {
  name: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
}

const NAV_ITEMS: NavItem[] = [
  { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { name: "Strategy Builder", href: "/strategies", icon: Sliders },
  { name: "Interactive Charts", href: "/charts", icon: TrendingUp },
  { name: "Backtesting Lab", href: "/backtest", icon: FlaskConical },
  { name: "Paper Trading", href: "/paper", icon: Terminal, badge: "LIVE" },
  { name: "Orders & Positions", href: "/orders", icon: ListOrdered },
  { name: "Risk & Audit", href: "/risk", icon: ShieldAlert },
  { name: "Settings & System", href: "/settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 flex-shrink-0 border-r border-[#1c2638] bg-[#0c1017] flex flex-col justify-between select-none">
      {/* Navigation Links */}
      <div className="py-4 px-2 space-y-1">
        <div className="px-3 pb-2 text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-500">
          Workstation Navigation
        </div>
        {NAV_ITEMS.map((item) => {
          const isActive =
            pathname === item.href ||
            (item.href !== "/dashboard" && pathname.startsWith(item.href)) ||
            (item.href === "/dashboard" && pathname === "/");
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group flex items-center justify-between px-3 py-2 rounded text-xs font-mono transition-colors ${
                isActive
                  ? "bg-[#162030] text-cyan-400 font-semibold border-l-2 border-cyan-400"
                  : "text-slate-400 hover:bg-[#121824] hover:text-slate-200"
              }`}
            >
              <div className="flex items-center space-x-2.5">
                <Icon
                  className={`w-4 h-4 transition-colors ${
                    isActive ? "text-cyan-400" : "text-slate-500 group-hover:text-slate-300"
                  }`}
                />
                <span>{item.name}</span>
              </div>
              {item.badge && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 font-bold tracking-wider">
                  {item.badge}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      {/* System Footer Diagnostics */}
      <div className="p-3 border-t border-[#1c2638] bg-[#090d13] text-[11px] font-mono space-y-2">
        <div className="flex items-center justify-between text-slate-400">
          <span>ENVIRONMENT</span>
          <span className="text-emerald-400 font-semibold flex items-center space-x-1">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span>PAPER MODE</span>
          </span>
        </div>
        <div className="flex items-center justify-between text-slate-400">
          <span>STRATEGY</span>
          <span className="text-slate-200">NIFTY ORB v1.0</span>
        </div>
        <div className="flex items-center justify-between text-slate-400">
          <span>RISK BREAKER</span>
          <span className="text-rose-400">₹3,000 LIMIT</span>
        </div>
        <div className="pt-2 border-t border-[#162030] flex items-center space-x-1.5 text-slate-500 text-[10px]">
          <Server className="w-3 h-3 text-slate-600" />
          <span>OMS FYERS v3 Adapter Connected</span>
        </div>
      </div>
    </aside>
  );
}

