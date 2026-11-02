"use client";

import React, { useState } from "react";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { KillSwitchModal } from "./KillSwitchModal";

export function WorkstationShell({ children }: { children: React.ReactNode }) {
  const [killSwitchOpen, setKillSwitchOpen] = useState(false);

  return (
    <div className="flex flex-col h-screen w-screen bg-[#07090e] text-slate-100 overflow-hidden font-sans">
      {/* Global Institutional Header */}
      <Header onOpenKillSwitch={() => setKillSwitchOpen(true)} />

      {/* Main Workstation Body: Sidebar + Dynamic Canvas */}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto bg-[#07090e] p-5 scrollbar-thin">
          {children}
        </main>
      </div>

      {/* Emergency Liquidation Modal */}
      <KillSwitchModal
        isOpen={killSwitchOpen}
        onClose={() => setKillSwitchOpen(false)}
        onSuccess={() => {
          // Trigger any needed global refresh
        }}
      />
    </div>
  );
}

