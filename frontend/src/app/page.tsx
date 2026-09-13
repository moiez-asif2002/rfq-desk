"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { RFQS_CHANGED } from "@/components/gmail-bar";
import { Button, Card, FlagChip, StatusBadge } from "@/components/ui";
import { api, money, timeAgo, type RfqStatus, type RfqSummary } from "@/lib/api";

const TABS = [
  { key: "open", label: "Open", statuses: "processing,needs_review,ready" },
  { key: "done", label: "Approved", statuses: "approved,exported" },
  { key: "closed", label: "Rejected & failed", statuses: "rejected,failed" },
  { key: "all", label: "All", statuses: "" },
] as const;

type Counts = Partial<Record<RfqStatus, number>>;

export default function QueuePage() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>(TABS[0]);
  const [items, setItems] = useState<RfqSummary[] | null>(null);
  const [counts, setCounts] = useState<Counts>({});
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .list(tab.statuses)
        .then((data) => {
          if (!alive) return;
          setItems(data.items);
          setCounts(data.counts);
          setError(null);
        })
        .catch((e: Error) => alive && setError(`Can't reach the API: ${e.message}`));
    load();
    const timer = setInterval(load, 3000);
    window.addEventListener(RFQS_CHANGED, load);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener(RFQS_CHANGED, load);
    };
  }, [tab]);

  async function upload(file: File) {
    setUploading(true);
    try {
      const { rfq_id } = await api.upload(file);
      router.push(`/rfqs/${rfq_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
      setUploading(false);
    }
  }

  const c = (s: RfqStatus) => counts[s] ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-zinc-500">
            RFQs arrive by Gmail, webhook or upload. AI extracts and matches them, and nothing goes out until a rep approves it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".eml,message/rfc822"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
          <Button variant="primary" onClick={() => fileInput.current?.click()} disabled={uploading}>
            {uploading ? "Uploading…" : "Upload .eml"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Needs review" value={c("needs_review")} tone="amber" />
        <Stat label="Ready to approve" value={c("ready")} tone="emerald" />
        <Stat label="Approved / exported" value={c("approved") + c("exported")} tone="indigo" />
        <Stat label="Rejected / failed" value={c("rejected") + c("failed")} tone="zinc" />
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">{error}</div>}

      <Card>
        <div className="flex gap-1 border-b border-zinc-100 px-3 pt-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
                tab.key === t.key ? "border-zinc-900 text-zinc-900" : "border-transparent text-zinc-500 hover:text-zinc-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">Quote</th>
                <th className="px-4 py-2.5 font-medium">Customer / subject</th>
                <th className="px-4 py-2.5 font-medium">Flags</th>
                <th className="px-4 py-2.5 text-right font-medium">Lines</th>
                <th className="px-4 py-2.5 text-right font-medium">Total</th>
                <th className="px-4 py-2.5 font-medium">Rep</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 text-right font-medium">Received</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {items?.map((r) => (
                <tr key={r.id} onClick={() => router.push(`/rfqs/${r.id}`)} className="cursor-pointer hover:bg-zinc-50">
                  <td className="whitespace-nowrap px-4 py-3">
                    <Link href={`/rfqs/${r.id}`} className="font-mono text-xs font-medium text-zinc-900 hover:underline">
                      {r.quote_number}
                    </Link>
                    <div className="mt-0.5 text-[11px] uppercase text-zinc-400">{r.source}</div>
                  </td>
                  <td className="max-w-md px-4 py-3">
                    <div className="truncate font-medium">{r.customer_company || r.customer_name || r.from_name || r.from_email}</div>
                    <div className="truncate text-xs text-zinc-500">{r.subject}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex max-w-56 flex-wrap gap-1">
                      {r.flag_codes.map((f) => (
                        <FlagChip key={f} code={f} />
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{r.line_count}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">{r.line_count ? money(r.total) : "—"}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-zinc-600">{r.assigned_rep ?? "—"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-xs text-zinc-500">{timeAgo(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {items && items.length === 0 && (
            <div className="px-4 py-14 text-center text-sm text-zinc-500">
              Nothing here yet. Upload a sample from <code className="font-mono text-xs">samples/eml</code>, sync Gmail,
              or run <code className="font-mono text-xs">scripts/send_samples.py</code>.
            </div>
          )}
          {!items && !error && <div className="px-4 py-14 text-center text-sm text-zinc-400">Loading…</div>}
        </div>
      </Card>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: "amber" | "emerald" | "indigo" | "zinc" }) {
  const bar = { amber: "bg-amber-400", emerald: "bg-emerald-500", indigo: "bg-indigo-500", zinc: "bg-zinc-300" }[tone];
  return (
    <div className="relative overflow-hidden rounded-xl border border-zinc-200 bg-white px-4 py-3 shadow-sm">
      <span className={`absolute inset-y-0 left-0 w-1 ${bar}`} />
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
