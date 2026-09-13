"use client";

import { useEffect, useState } from "react";
import { api, gmailConnectUrl, timeAgo, type GmailStatus } from "@/lib/api";
import { Button } from "@/components/ui";

export const RFQS_CHANGED = "rfqs:changed";

export default function GmailBar() {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api.gmailStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function sync() {
    setSyncing(true);
    setMessage(null);
    try {
      const { created } = await api.gmailSync();
      setMessage(created.length ? `${created.length} new` : "No new RFQs");
      window.dispatchEvent(new Event(RFQS_CHANGED));
      setStatus(await api.gmailStatus());
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  if (!status) return null;

  if (!status.connected) {
    return (
      <a
        href={gmailConnectUrl}
        className="inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-zinc-300 hover:bg-zinc-50"
      >
        <GmailIcon /> Connect Gmail
      </a>
    );
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="hidden items-center gap-2 text-zinc-600 md:flex">
        <span className="size-2 rounded-full bg-emerald-500" />
        {status.email}
        {status.last_synced_at && <span className="text-zinc-400">· synced {timeAgo(status.last_synced_at)}</span>}
      </span>
      {message && <span className="text-xs text-zinc-500">{message}</span>}
      <Button onClick={sync} disabled={syncing}>
        <GmailIcon /> {syncing ? "Syncing…" : "Sync inbox"}
      </Button>
    </div>
  );
}

function GmailIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path fill="#4285F4" d="M22 6.5V18a2 2 0 0 1-2 2h-2V9.3l-6 4.5-6-4.5V20H4a2 2 0 0 1-2-2V6.5l2 1.5 8 6 8-6z" />
      <path fill="#EA4335" d="M22 6.5 12 14 2 6.5V6a2 2 0 0 1 3.2-1.6L12 9.5l6.8-5.1A2 2 0 0 1 22 6z" />
    </svg>
  );
}
