"use client";

import { useEffect, useRef } from "react";

import type { PerformanceReport } from "@/lib/types";

/* ── price chart ──────────────────────────────────────────────────────────── */

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

/**
 * TradingView Lightweight Charts. Loaded dynamically so a chart failure can
 * never blank the page around it, and drawn with the same palette tokens as the
 * rest of the terminal so bid/ask colour keeps its meaning.
 */
export function PriceChart({
  candles, levels, height = 380,
}: {
  candles: Candle[];
  levels?: { price: number; label: string; color: "long" | "short" | "neutral" }[];
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<any>(null);

  useEffect(() => {
    if (!container.current || candles.length === 0) return;
    let disposed = false;

    (async () => {
      const lib = await import("lightweight-charts");
      if (disposed || !container.current) return;

      const c = lib.createChart(container.current, {
        height,
        layout: { background: { color: "transparent" }, textColor: "#8593A6", fontSize: 11,
                  fontFamily: "'JetBrains Mono', monospace" },
        grid: { vertLines: { color: "#151C26" }, horzLines: { color: "#151C26" } },
        rightPriceScale: { borderColor: "#22303F" },
        timeScale: { borderColor: "#22303F", timeVisible: true, secondsVisible: false },
        crosshair: {
          mode: 0,
          vertLine: { color: "#5C6979", width: 1, style: 3, labelBackgroundColor: "#1D2733" },
          horzLine: { color: "#5C6979", width: 1, style: 3, labelBackgroundColor: "#1D2733" },
        },
        handleScale: { axisPressedMouseMove: { price: false } },
      });
      chart.current = c;

      const series = c.addCandlestickSeries({
        upColor: "#3DD68C", downColor: "#FF5C6C",
        borderUpColor: "#3DD68C", borderDownColor: "#FF5C6C",
        wickUpColor: "#3DD68C", wickDownColor: "#FF5C6C",
      });
      series.setData(candles as any);

      for (const level of levels ?? []) {
        series.createPriceLine({
          price: level.price,
          color: level.color === "long" ? "#3DD68C" : level.color === "short" ? "#FF5C6C" : "#8593A6",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: level.label,
        });
      }

      c.timeScale().fitContent();

      const resize = () => container.current && c.applyOptions({ width: container.current.clientWidth });
      resize();
      window.addEventListener("resize", resize);
      return () => window.removeEventListener("resize", resize);
    })();

    return () => {
      disposed = true;
      chart.current?.remove?.();
      chart.current = null;
    };
  }, [candles, levels, height]);

  if (candles.length === 0) {
    return (
      <div className="flex items-center justify-center text-tick text-ink-400" style={{ height }}>
        No price history to draw yet.
      </div>
    );
  }
  return <div ref={container} style={{ height }} />;
}

/* ── sparkline ────────────────────────────────────────────────────────────── */

export function Sparkline({
  values, height = 40, tone = "auto",
}: {
  values: number[];
  height?: number;
  tone?: "auto" | "long" | "short" | "neutral";
}) {
  if (values.length < 2) {
    return <div style={{ height }} className="flex items-center text-micro text-ink-400">No data</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const width = 100;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * width},${height - ((v - min) / range) * height}`)
    .join(" ");

  const rising = values[values.length - 1] >= values[0];
  const stroke =
    tone === "auto" ? (rising ? "#3DD68C" : "#FF5C6C")
      : tone === "long" ? "#3DD68C"
      : tone === "short" ? "#FF5C6C"
      : "#8593A6";

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/* ── equity curve with drawdown ───────────────────────────────────────────── */

export function EquityCurve({
  points, height = 240,
}: {
  points: PerformanceReport["equity_curve"];
  height?: number;
}) {
  if (points.length < 2) {
    return (
      <div className="flex items-center justify-center text-tick text-ink-400" style={{ height }}>
        The curve appears once signals start closing.
      </div>
    );
  }

  const width = 800;
  const values = points.map((p) => p.cumulative_pct);
  const drawdowns = points.map((p) => p.drawdown_pct);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const range = max - min || 1;
  const ddMax = Math.max(...drawdowns) || 1;

  const equityHeight = height * 0.68;
  const ddHeight = height * 0.24;

  const x = (i: number) => (i / (points.length - 1)) * width;
  const y = (v: number) => equityHeight - ((v - min) / range) * equityHeight;

  const line = points.map((p, i) => `${x(i)},${y(p.cumulative_pct)}`).join(" ");
  const area = `${x(0)},${y(min)} ${line} ${x(points.length - 1)},${y(min)}`;
  const zeroY = y(0);

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         role="img" aria-label="Cumulative return and drawdown">
      <defs>
        <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3DD68C" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#3DD68C" stopOpacity="0" />
        </linearGradient>
      </defs>

      <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="#22303F" strokeWidth="1" strokeDasharray="3 3" />
      <polygon points={area} fill="url(#equityFill)" />
      <polyline points={line} fill="none" stroke="#3DD68C" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />

      <g transform={`translate(0, ${equityHeight + height * 0.08})`}>
        {points.map((p, i) => (
          <rect
            key={i}
            x={x(i)}
            y={0}
            width={Math.max(width / points.length - 0.5, 0.6)}
            height={(p.drawdown_pct / ddMax) * ddHeight}
            fill="#FF5C6C"
            opacity="0.55"
          />
        ))}
      </g>
    </svg>
  );
}

/* ── calibration / reliability diagram ────────────────────────────────────── */

/**
 * Predicted win rate against observed win rate. The diagonal is perfect
 * calibration. Bins with too few closed signals are drawn as gaps rather than
 * filled in — an empty bin is information, and inventing a point there would be
 * the exact dishonesty this chart exists to expose.
 */
export function CalibrationChart({
  curve, height = 260,
}: {
  curve: PerformanceReport["calibration_curve"];
  height?: number;
}) {
  const filled = curve.filter((b) => b.n > 0 && b.observed !== null);
  if (filled.length < 2) {
    return (
      <div className="flex items-center justify-center text-center px-6 text-tick text-ink-400"
           style={{ height }}>
        Not enough closed signals yet to plot calibration. This chart appears once each confidence
        band has a meaningful sample.
      </div>
    );
  }

  const size = 260;
  const pad = 28;
  const inner = size - pad * 2;
  const px = (v: number) => pad + v * inner;
  const py = (v: number) => size - pad - v * inner;
  const maxN = Math.max(...filled.map((b) => b.n));

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${size} ${size}`} role="img"
         aria-label="Reliability diagram">
      <line x1={px(0)} y1={py(0)} x2={px(1)} y2={py(1)} stroke="#22303F" strokeWidth="1" strokeDasharray="3 3" />
      <line x1={pad} y1={pad} x2={pad} y2={size - pad} stroke="#22303F" />
      <line x1={pad} y1={size - pad} x2={size - pad} y2={size - pad} stroke="#22303F" />

      {filled.map((b, i) => {
        const cx = px(b.predicted ?? 0);
        const cy = py(b.observed ?? 0);
        return (
          <g key={i}>
            {b.ci_low !== null && b.ci_high !== null && (
              <line x1={cx} y1={py(b.ci_low)} x2={cx} y2={py(b.ci_high)} stroke="#5C6979" strokeWidth="1" />
            )}
            <circle cx={cx} cy={cy} r={3 + (b.n / maxN) * 4} fill="#3DD68C" fillOpacity="0.85" />
          </g>
        );
      })}

      <text x={size / 2} y={size - 6} textAnchor="middle" fill="#5C6979" fontSize="9"
            fontFamily="'JetBrains Mono', monospace">
        predicted win rate
      </text>
      <text x={10} y={size / 2} textAnchor="middle" fill="#5C6979" fontSize="9"
            fontFamily="'JetBrains Mono', monospace" transform={`rotate(-90 10 ${size / 2})`}>
        observed win rate
      </text>
    </svg>
  );
}
