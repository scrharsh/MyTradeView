"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineStyle,
  UTCTimestamp,
  SeriesMarker,
  LineWidth,
} from "lightweight-charts";
import { CandleItem } from "../types/api";
import { formatNumber, formatISTTime } from "../lib/formatters";

interface TradingChartProps {
  spotCandles: CandleItem[];
  optionCandles: CandleItem[];
  orbHigh?: number;
  orbLow?: number;
  breakoutTime?: string;
  optionEntryPrice?: number;
  optionSymbol?: string;
}

export function TradingChart({
  spotCandles,
  optionCandles,
  orbHigh,
  orbLow,
  breakoutTime,
  optionEntryPrice,
  optionSymbol = "NSE:NIFTY26NOV25000CE",
}: TradingChartProps) {
  const spotChartContainerRef = useRef<HTMLDivElement>(null);
  const optionChartContainerRef = useRef<HTMLDivElement>(null);

  const spotChartRef = useRef<IChartApi | null>(null);
  const optionChartRef = useRef<IChartApi | null>(null);

  // HUD state for crosshair
  const [spotHud, setSpotHud] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  } | null>(null);

  const [optionHud, setOptionHud] = useState<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
  } | null>(null);

  useEffect(() => {
    if (!spotChartContainerRef.current || !optionChartContainerRef.current) return;

    // Common chart options
    const commonOptions = {
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
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#38bdf8", width: 1 as LineWidth, style: LineStyle.Dashed },
        horzLine: { color: "#38bdf8", width: 1 as LineWidth, style: LineStyle.Dashed },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#1e293b",
      },
    };

    // 1. Create Spot Chart
    const spotChart = createChart(spotChartContainerRef.current, {
      ...commonOptions,
      height: 320,
    });
    spotChartRef.current = spotChart;

    const spotSeries = spotChart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    // 2. Create Option Chart
    const optionChart = createChart(optionChartContainerRef.current, {
      ...commonOptions,
      height: 240,
    });
    optionChartRef.current = optionChart;

    const optionSeries = optionChart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });

    // Format Spot Candles
    if (spotCandles && spotCandles.length > 0) {
      // Deduplicate and sort by epoch ascending
      const sortedSpot = [...spotCandles]
        .map((c) => ({
          time: Math.floor(new Date(c.timestamp).getTime() / 1000) as UTCTimestamp,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          volume: Number(c.volume),
          origTime: c.timestamp,
        }))
        .filter((c, idx, arr) => idx === 0 || c.time > arr[idx - 1].time)
        .sort((a, b) => a.time - b.time);

      spotSeries.setData(sortedSpot);

      // Add ORB Boundary Lines
      if (orbHigh) {
        spotSeries.createPriceLine({
          price: orbHigh,
          color: "#38bdf8",
          lineWidth: 2 as LineWidth,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `ORB HIGH (${formatNumber(orbHigh, 2)})`,
        });
      }

      if (orbLow) {
        spotSeries.createPriceLine({
          price: orbLow,
          color: "#f59e0b",
          lineWidth: 2 as LineWidth,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `ORB LOW (${formatNumber(orbLow, 2)})`,
        });
      }

      // Add Breakout Marker at 09:46 candle
      const breakoutCandle = sortedSpot.find((c) => c.origTime.includes("09:46"));
      if (breakoutCandle) {
        const markers: SeriesMarker<UTCTimestamp>[] = [
          {
            time: breakoutCandle.time,
            position: "belowBar",
            color: "#38bdf8",
            shape: "arrowUp",
            text: "ORB BREAKOUT ENTRY",
          },
        ];
        spotSeries.setMarkers(markers);
      }

      // Default HUD
      const last = sortedSpot[sortedSpot.length - 1];
      setSpotHud({
        time: last.origTime,
        open: last.open,
        high: last.high,
        low: last.low,
        close: last.close,
        volume: last.volume,
      });

      spotChart.timeScale().fitContent();
    }

    // Format Option Candles
    if (optionCandles && optionCandles.length > 0) {
      const sortedOpt = [...optionCandles]
        .map((c) => ({
          time: Math.floor(new Date(c.timestamp).getTime() / 1000) as UTCTimestamp,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          origTime: c.timestamp,
        }))
        .filter((c, idx, arr) => idx === 0 || c.time > arr[idx - 1].time)
        .sort((a, b) => a.time - b.time);

      optionSeries.setData(sortedOpt);

      // Add Entry Price Line
      if (optionEntryPrice) {
        optionSeries.createPriceLine({
          price: optionEntryPrice,
          color: "#10b981",
          lineWidth: 2 as LineWidth,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `ENTRY FILL @ ₹${optionEntryPrice}`,
        });
      }

      const lastOpt = sortedOpt[sortedOpt.length - 1];
      setOptionHud({
        time: lastOpt.origTime,
        open: lastOpt.open,
        high: lastOpt.high,
        low: lastOpt.low,
        close: lastOpt.close,
      });

      optionChart.timeScale().fitContent();
    }

    // Crosshair listeners
    spotChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) return;
      const data = param.seriesData.get(spotSeries) as any;
      if (data) {
        setSpotHud({
          time: new Date((param.time as number) * 1000).toISOString(),
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
          volume: data.volume || 0,
        });
      }
    });

    optionChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) return;
      const data = param.seriesData.get(optionSeries) as any;
      if (data) {
        setOptionHud({
          time: new Date((param.time as number) * 1000).toISOString(),
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
        });
      }
    });

    // Auto-resize handlers
    const handleResize = () => {
      if (spotChartContainerRef.current) {
        spotChart.applyOptions({ width: spotChartContainerRef.current.clientWidth });
      }
      if (optionChartContainerRef.current) {
        optionChart.applyOptions({ width: optionChartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      spotChart.remove();
      optionChart.remove();
    };
  }, [spotCandles, optionCandles, orbHigh, orbLow, breakoutTime, optionEntryPrice]);

  return (
    <div className="space-y-4">
      {/* Upper Panel: Spot Index */}
      <div className="panel p-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
          <div className="flex items-center space-x-3">
            <span className="font-bold text-slate-100 text-sm">NSE:NIFTY50-INDEX</span>
            <span className="text-[10px] bg-blue-950 text-blue-400 border border-blue-800 px-1.5 py-0.5 rounded font-semibold">
              SPOT 1m
            </span>
            <span className="text-slate-400">ORB HIGH: {formatNumber(orbHigh, 2)}</span>
            <span className="text-slate-400">ORB LOW: {formatNumber(orbLow, 2)}</span>
          </div>

          {spotHud && (
            <div className="flex items-center space-x-3 text-[11px] text-slate-400">
              <span>Time: <strong className="text-slate-200">{formatISTTime(spotHud.time)}</strong></span>
              <span>O: <strong className="text-slate-200">{formatNumber(spotHud.open, 2)}</strong></span>
              <span>H: <strong className="text-emerald-400">{formatNumber(spotHud.high, 2)}</strong></span>
              <span>L: <strong className="text-rose-400">{formatNumber(spotHud.low, 2)}</strong></span>
              <span>C: <strong className="text-slate-100">{formatNumber(spotHud.close, 2)}</strong></span>
            </div>
          )}
        </div>

        <div ref={spotChartContainerRef} className="w-full h-[320px] rounded overflow-hidden" />
      </div>

      {/* Lower Panel: Option Contract */}
      <div className="panel p-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between border-b border-[#1c2638] pb-2 font-mono text-xs">
          <div className="flex items-center space-x-3">
            <span className="font-bold text-slate-100 text-sm">{optionSymbol}</span>
            <span className="text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 px-1.5 py-0.5 rounded font-semibold">
              OPTION 1m (DERIVATIVE)
            </span>
            {optionEntryPrice !== undefined && (
              <span className="text-slate-400">ENTRY: ₹{optionEntryPrice.toFixed(2)}</span>
            )}
          </div>

          {optionHud && (
            <div className="flex items-center space-x-3 text-[11px] text-slate-400">
              <span>Time: <strong className="text-slate-200">{formatISTTime(optionHud.time)}</strong></span>
              <span>O: <strong className="text-slate-200">{formatNumber(optionHud.open, 2)}</strong></span>
              <span>H: <strong className="text-emerald-400">{formatNumber(optionHud.high, 2)}</strong></span>
              <span>L: <strong className="text-rose-400">{formatNumber(optionHud.low, 2)}</strong></span>
              <span>C: <strong className="text-slate-100">{formatNumber(optionHud.close, 2)}</strong></span>
            </div>
          )}
        </div>

        <div ref={optionChartContainerRef} className="w-full h-[240px] rounded overflow-hidden" />
      </div>
    </div>
  );
}

