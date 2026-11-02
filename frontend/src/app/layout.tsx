import type { Metadata } from "next";
import "./globals.css";
import { WorkstationShell } from "../components/WorkstationShell";

export const metadata: Metadata = {
  title: "MyTradeView | Institutional NIFTY Trading Workstation",
  description:
    "Institutional algorithmic trading workstation for NIFTY ORB options strategy with discrete-event backtesting, risk management, and FYERS OMS paper execution.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased bg-[#07090e] text-slate-100 min-h-screen">
        <WorkstationShell>{children}</WorkstationShell>
      </body>
    </html>
  );
}

