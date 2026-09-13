import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import GmailBar from "@/components/gmail-bar";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RFQ Desk",
  description: "RFQ emails and PDFs in, reviewed quotes out.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-zinc-50 text-zinc-900">
        <header className="border-b border-zinc-200 bg-white">
          <div className="mx-auto flex h-14 w-full max-w-7xl items-center justify-between gap-4 px-6">
            <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
              <span className="grid size-7 place-items-center rounded-md bg-zinc-900 font-mono text-[11px] font-bold text-white">
                RQ
              </span>
              RFQ Desk
              <span className="hidden text-sm font-normal text-zinc-400 sm:inline">· Acme Industrial Supply</span>
            </Link>
            <GmailBar />
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
