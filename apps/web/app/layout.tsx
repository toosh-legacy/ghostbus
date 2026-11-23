import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { Suspense } from "react";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "GhostBus — is my bus actually coming?",
  description: "Honest arrival ranges, ghost-bus risk and route reliability for MBTA buses.",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1e3a8a" };

const nav = [
  ["/", "Stops"],
  ["/reliability", "Reliability"],
  ["/ask", "Ask"],
  ["/methodology", "Methodology"],
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:p-2 focus:bg-white">
          Skip to content
        </a>
        <header className="bg-blue-900 text-white">
          <nav aria-label="Main" className="mx-auto max-w-4xl flex flex-wrap items-center gap-4 p-3">
            <Link href="/" className="font-bold text-lg">
              GhostBus
            </Link>
            {nav.map(([href, label]) => (
              <Link key={href} href={href} className="underline-offset-4 hover:underline focus:underline">
                {label}
              </Link>
            ))}
          </nav>
        </header>
        <Providers>
          <main id="main" className="mx-auto w-full max-w-4xl flex-1 p-4">
            <Suspense fallback={<p>Loading…</p>}>{children}</Suspense>
          </main>
        </Providers>
        <footer className="text-sm text-slate-700 p-4 mx-auto max-w-4xl">
          Data: MBTA GTFS and GTFS-Realtime (MassDOT Developers License), MBTA Bus Arrival Departure Times (CC0). Map
          © OpenStreetMap contributors. Not an official MBTA product.
        </footer>
      </body>
    </html>
  );
}
