import type { Metadata } from "next";
import "./globals.css";
import { Footer, Nav, StatusBar } from "@/components/nav";

export const metadata: Metadata = {
  title: "SignalProof — verifiable crypto signals",
  description:
    "Deterministic BTC, ETH and SOL trading signals published to an append-only ledger. Every signal, including the losses, stays on the public record.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:top-2 focus:left-2 focus:btn-primary"
        >
          Skip to content
        </a>
        <Nav />
        <StatusBar />
        <main id="main" className="mx-auto max-w-[1600px] px-4 py-5">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
