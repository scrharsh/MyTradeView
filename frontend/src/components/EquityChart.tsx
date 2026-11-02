"use client";

import React, { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  IChartApi,
  UTCTimestamp,
  LineStyle,
  LineWidth,
} from "lightweight-charts";
import { BacktestEquityPoint } from "../types/api";

interface EquityChartProps {
  equityCurve: BacktestEquityPoint[];
}

export function EquityChart({ equityCurve }: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || !equityCurve || equityCurve.length === 0) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0c1017" },
        textColor: "#94a3b8",
        fontSize: 11,
        fontFamily: "monospace",
      },
      grid: {
        vertLines: { color: "#162030" },
        horzLines: { color: "#162030" },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: "#1e293b",
      },
      height: 280,
    });
    chartRef.current = chart;

    // Equity Area Series
    const areaSeries = chart.addAreaSeries({
      topColor: "rgba(56, 189, 248, 0.4)",
      bottomColor: "rgba(56, 189, 248, 0.0)",
      lineColor: "#38bdf8",
      lineWidth: 2 as LineWidth,
    });

    const formattedData = equityCurve
      .map((pt) => ({
        time: Math.floor(new Date(pt.timestamp).getTime() / 1000) as UTCTimestamp,
        value: Number(pt.equity),
      }))
      .filter((pt, idx, arr) => idx === 0 || pt.time > arr[idx - 1].time)
      .sort((a, b) => a.time - b.time);

    areaSeries.setData(formattedData);
    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [equityCurve]);

  return <div ref={containerRef} className="w-full h-[280px] rounded overflow-hidden" />;
}

