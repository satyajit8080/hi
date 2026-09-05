import type { Config } from "tailwindcss";

/**
 * Palette notes (see docs/DESIGN.md):
 *  - Base is a cold graphite, not tinted near-black, so long/short colour reads
 *    cleanly against it on a trading floor monitor.
 *  - Exactly two semantic accents (bid/long = mint, ask/short = coral) because
 *    in a book/ladder those colours carry meaning, plus amber reserved solely
 *    for degraded data. Nothing else gets a colour.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: { 900: "#0A0E14", 800: "#0F141C", 700: "#151C26", 600: "#1D2733", 500: "#283444" },
        ink: { 100: "#E8EDF4", 200: "#B7C2D0", 300: "#8593A6", 400: "#5C6979" },
        long: { DEFAULT: "#3DD68C", dim: "#1B4433", deep: "#0E2A1F" },
        short: { DEFAULT: "#FF5C6C", dim: "#4A1F26", deep: "#2C1216" },
        warn: { DEFAULT: "#F2B441", dim: "#463317" },
        edge: "#22303F",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        micro: ["10.5px", { lineHeight: "14px", letterSpacing: "0.01em" }],
        tick: ["11.5px", { lineHeight: "16px" }],
      },
      boxShadow: { panel: "inset 0 1px 0 0 rgba(255,255,255,0.03)" },
      borderRadius: { panel: "3px" },
    },
  },
  plugins: [],
};
export default config;
