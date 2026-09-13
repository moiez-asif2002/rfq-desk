"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ProductPicker from "@/components/product-picker";
import { Button, Card, Confidence, FlagChip, StatusBadge } from "@/components/ui";
import { ApiError, api, attachmentUrl, money, timeAgo, type Flag, type Line, type RfqDetail } from "@/lib/api";

const OPEN_STATUSES = ["needs_review", "ready"];

export default function RfqPage() {
  const { id: rawId } = useParams<{ id: string }>();
  const id = Number(rawId);
  const [rfq, setRfq] = useState<RfqDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const polling = rfq?.status === "processing" || (rfq?.status === "approved" && !rfq.deliveries.some((d) => d.status === "failed"));

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get(id)
        .then((r) => alive && setRfq(r))
        .catch((e: Error) => alive && setError(e.message));
    load();
    if (!polling) return () => void (alive = false);
    const timer = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [id, polling]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setRfq(result && typeof result === "object" && "lines" in result ? (result as RfqDetail) : await api.get(id));
    } catch (e) {
      if (e instanceof ApiError && e.problems.length) setError(`${e.message}: ${e.problems.join(" ")}`);
      else setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  if (!rfq) {
    return <div className="py-20 text-center text-sm text-zinc-500">{error ?? "Loading…"}</div>;
  }

  const editable = OPEN_STATUSES.includes(rfq.status) && !busy;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-800">
            ← Review queue
          </Link>
          <div className="mt-1 flex items-center gap-3">
            <h1 className="font-mono text-lg font-semibold">{rfq.quote_number}</h1>
            <StatusBadge status={rfq.status} />
          </div>
          <p className="mt-0.5 text-sm text-zinc-500">
            {rfq.customer_company || rfq.from_email} · assigned to{" "}
            <span className="text-zinc-800">{rfq.assigned_rep ?? "unassigned"}</span>
          </p>
        </div>
        <Actions rfq={rfq} busy={busy} run={run} />
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">{error}</div>}

      {rfq.status === "processing" && (
        <div className="flex items-center gap-3 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
          <span className="size-2 animate-pulse rounded-full bg-sky-500" />
          Gemini is reading the email and attachments, then matching lines against the catalog…
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-5">
          <EmailPanel rfq={rfq} />
        </div>

        <div className="space-y-4 lg:col-span-7">
          {rfq.review_flags.length > 0 && <FlagsPanel flags={rfq.review_flags} />}
          <CustomerPanel rfq={rfq} editable={editable} run={run} />
          <LinesPanel rfq={rfq} editable={editable} run={run} />
          {(rfq.draft_reply || rfq.deliveries.length > 0) && <OutcomePanel rfq={rfq} busy={busy} run={run} />}
          <AuditPanel rfq={rfq} />
        </div>
      </div>
    </div>
  );
}

type Run = (action: () => Promise<unknown>) => Promise<void>;

function Actions({ rfq, busy, run }: { rfq: RfqDetail; busy: boolean; run: Run }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const open = OPEN_STATUSES.includes(rfq.status);
  const blocked = rfq.approval_problems.length > 0;

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        {["needs_review", "ready", "failed", "rejected"].includes(rfq.status) && (
          <Button variant="ghost" disabled={busy} onClick={() => run(() => api.reprocess(rfq.id))}>
            Re-run extraction
          </Button>
        )}
        {(open || rfq.status === "failed") && (
          <Button variant="danger" disabled={busy} onClick={() => setRejecting((r) => !r)}>
            Reject
          </Button>
        )}
        {open && (
          <Button variant="primary" disabled={busy || blocked} onClick={() => run(() => api.approve(rfq.id))}>
            Approve & draft reply
          </Button>
        )}
      </div>
      {rejecting && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (reason.trim()) run(() => api.reject(rfq.id, reason.trim())).then(() => setRejecting(false));
          }}
        >
          <input
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (e.g. not a customer)"
            className="w-64 rounded-lg border border-zinc-300 px-2 py-1.5 text-sm"
          />
          <Button variant="danger" type="submit" disabled={!reason.trim()}>
            Confirm reject
          </Button>
        </form>
      )}
      {open && blocked && (
        <ul className="max-w-md text-right text-xs text-amber-800">
          {rfq.approval_problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmailPanel({ rfq }: { rfq: RfqDetail }) {
  const [active, setActive] = useState(0);
  const attachment = rfq.message.attachments[active];
  return (
    <Card title="Inbound email" action={<span className="text-[11px] uppercase text-zinc-400">via {rfq.source}</span>}>
      <div className="space-y-1 border-b border-zinc-100 px-4 py-3 text-sm">
        <div className="font-medium">{rfq.subject}</div>
        <div className="text-xs text-zinc-500">
          {rfq.from_name} &lt;{rfq.from_email}&gt; · {new Date(rfq.received_at).toLocaleString()}
        </div>
      </div>
      <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap px-4 py-3 font-sans text-sm text-zinc-700">
        {rfq.message.body_text || "(no body)"}
      </pre>
      {attachment && (
        <div className="border-t border-zinc-100">
          <div className="flex gap-1 overflow-x-auto px-3 pt-2">
            {rfq.message.attachments.map((a, i) => (
              <button
                key={a.id}
                onClick={() => setActive(i)}
                className={`rounded-md px-2 py-1 text-xs ${i === active ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-700"}`}
              >
                📎 {a.filename} <span className="opacity-60">{Math.round(a.size_bytes / 1024)} KB</span>
              </button>
            ))}
          </div>
          <div className="p-3">
            {attachment.content_type.startsWith("image/") ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={attachmentUrl(attachment.id)} alt={attachment.filename} className="w-full rounded border" />
            ) : attachment.content_type === "application/pdf" ? (
              <iframe
                src={attachmentUrl(attachment.id)}
                title={attachment.filename}
                className="h-[600px] w-full rounded border border-zinc-200"
              />
            ) : (
              <a href={attachmentUrl(attachment.id)} className="text-sm text-indigo-700 underline">
                Download {attachment.filename}
              </a>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function FlagsPanel({ flags }: { flags: Flag[] }) {
  const injection = flags.find((f) => f.code === "prompt_injection");
  const others = flags.filter((f) => f.code !== "prompt_injection");
  return (
    <div className="space-y-2">
      {injection && (
        <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-800">
            ⚠ Prompt-injection attempt detected
          </div>
          <p className="mt-1 text-sm text-red-700">
            The document contains instructions aimed at the AI. They were treated as data and ignored, and prices still
            come only from the catalog. Check this RFQ carefully before approving.
          </p>
          {injection.evidence?.map((e) => (
            <blockquote key={e} className="mt-2 border-l-2 border-red-300 pl-3 font-mono text-xs text-red-900">
              “{e}”
            </blockquote>
          ))}
        </div>
      )}
      {others.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3">
          <div className="text-sm font-semibold text-amber-900">Why this needs a human</div>
          <ul className="mt-2 space-y-1.5">
            {others.map((f, i) => (
              <li key={`${f.code}-${i}`} className="flex items-start gap-2 text-sm text-amber-950">
                <FlagChip code={f.code} />
                <span>{f.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CustomerPanel({ rfq, editable, run }: { rfq: RfqDetail; editable: boolean; run: Run }) {
  const save = (field: keyof RfqDetail) => (value: string) =>
    run(() => api.update(rfq.id, { [field]: value.trim() === "" ? null : value.trim() }));
  const fields: [keyof RfqDetail, string, string][] = [
    ["customer_name", "Contact", "text"],
    ["customer_company", "Company", "text"],
    ["customer_email", "Quote to (email)", "email"],
    ["customer_phone", "Phone", "text"],
    ["requested_delivery_date", "Needed by", "date"],
    ["ship_to", "Ship to", "text"],
  ];
  return (
    <Card
      title="Customer & delivery"
      action={
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          extraction confidence <Confidence value={rfq.header_confidence} />
        </span>
      }
    >
      <div className="grid gap-3 px-4 py-3 sm:grid-cols-2">
        {fields.map(([field, label, type]) => {
          const value = (rfq[field] as string | null) ?? "";
          return (
            <label key={field} className={field === "ship_to" ? "sm:col-span-2" : ""}>
              <span className="text-xs font-medium text-zinc-500">{label}</span>
              <input
                key={`${field}:${value}`}
                type={type}
                defaultValue={value}
                disabled={!editable}
                onBlur={(e) => e.target.value !== value && save(field)(e.target.value)}
                className={`mt-0.5 w-full rounded-md border px-2 py-1.5 text-sm disabled:bg-zinc-50 disabled:text-zinc-600 ${
                  !value ? "border-amber-300 bg-amber-50/50" : "border-zinc-300"
                }`}
              />
            </label>
          );
        })}
        {rfq.notes && (
          <div className="text-sm sm:col-span-2">
            <span className="text-xs font-medium text-zinc-500">Notes from customer</span>
            <p className="mt-0.5 text-zinc-700">{rfq.notes}</p>
          </div>
        )}
      </div>
    </Card>
  );
}

function LinesPanel({ rfq, editable, run }: { rfq: RfqDetail; editable: boolean; run: Run }) {
  return (
    <Card
      title={`Line items (${rfq.lines.length})`}
      action={
        rfq.extraction_model && (
          <span className="font-mono text-[11px] text-zinc-400">
            {rfq.extraction_model} · prompt {rfq.prompt_version} · {((rfq.extraction_ms ?? 0) / 1000).toFixed(1)}s
          </span>
        )
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wide text-zinc-500">
            <tr className="border-b border-zinc-100">
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Customer asked for</th>
              <th className="px-3 py-2 font-medium">Catalog match</th>
              <th className="px-3 py-2 text-right font-medium">Qty</th>
              <th className="px-3 py-2 text-right font-medium">Unit</th>
              <th className="px-3 py-2 text-right font-medium">Ext.</th>
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {rfq.lines.map((line) => (
              <LineRow key={line.id} rfqId={rfq.id} line={line} editable={editable} run={run} />
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-zinc-200">
              <td colSpan={5} className="px-3 py-2.5 text-right text-sm font-medium text-zinc-600">
                Total (excl. tax & freight)
              </td>
              <td className="px-3 py-2.5 text-right font-semibold tabular-nums">{money(rfq.total)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
        {rfq.lines.length === 0 && rfq.status !== "processing" && (
          <div className="px-4 py-8 text-center text-sm text-zinc-500">No line items.</div>
        )}
      </div>
    </Card>
  );
}

const MATCH_STYLE: Record<Line["match_status"], string> = {
  matched: "bg-emerald-50 text-emerald-700",
  manual: "bg-indigo-50 text-indigo-700",
  ambiguous: "bg-amber-100 text-amber-900",
  unmatched: "bg-red-50 text-red-700",
};

function LineRow({ rfqId, line, editable, run }: { rfqId: number; line: Line; editable: boolean; run: Run }) {
  const p = line.product;
  const short = p && line.quantity != null && p.stock_qty < line.quantity;
  const numberInput = (field: "quantity" | "unit_price", value: number | null, step: string) => (
    <input
      key={`${field}:${value}`}
      type="number"
      min="0"
      step={step}
      defaultValue={value ?? ""}
      disabled={!editable}
      onBlur={(e) => {
        const next = e.target.value === "" ? null : Number(e.target.value);
        if (next !== null && next !== value) run(() => api.updateLine(rfqId, line.id, { [field]: next }));
      }}
      className={`w-20 rounded-md border px-1.5 py-1 text-right text-sm tabular-nums disabled:border-transparent disabled:bg-transparent ${
        value == null ? "border-amber-300 bg-amber-50" : "border-zinc-300"
      }`}
    />
  );

  return (
    <tr className="align-top">
      <td className="px-3 py-3 text-xs text-zinc-400">{line.line_no}</td>
      <td className="max-w-52 px-3 py-3">
        {line.raw_part_number && <div className="font-mono text-xs font-medium">{line.raw_part_number}</div>}
        <div className="text-xs text-zinc-600">{line.raw_description}</div>
        <div className="mt-0.5 text-[11px] text-zinc-400">
          read with <Confidence value={line.confidence} /> confidence{line.uom ? ` · ${line.uom}` : ""}
        </div>
      </td>
      <td className="max-w-64 px-3 py-3">
        {p ? (
          <>
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs font-medium">{p.sku}</span>
              <span className={`rounded px-1 py-px text-[10px] font-medium ${MATCH_STYLE[line.match_status]}`}>
                {line.match_status === "manual"
                  ? "set by rep"
                  : line.match_method === "exact_sku"
                    ? "exact SKU"
                    : `${line.match_method === "ai_rerank" ? "AI " : ""}${Math.round((line.match_score ?? 0) * 100)}%`}
              </span>
            </div>
            <div className="text-xs text-zinc-600">{p.name}</div>
            <div className={`text-[11px] ${short ? "text-amber-700" : "text-zinc-400"}`}>
              {short ? `only ${p.stock_qty} in stock · ${p.lead_time_days}d lead time` : `${p.stock_qty} in stock`}
            </div>
          </>
        ) : (
          <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${MATCH_STYLE[line.match_status]}`}>
            {line.match_status === "ambiguous" ? "Confirm match" : "No catalog match"}
          </span>
        )}
        {line.match_reason && line.match_status !== "manual" && (
          <div className="mt-1 text-[11px] leading-snug text-violet-700">
            <span className="font-medium">AI:</span> {line.match_reason}
          </div>
        )}
        <div className="mt-1">
          <ProductPicker
            candidates={line.candidates}
            disabled={!editable}
            label={p ? "Change product" : "Choose product →"}
            onPick={(productId) => run(() => api.updateLine(rfqId, line.id, { product_id: productId }))}
          />
        </div>
      </td>
      <td className="px-3 py-3 text-right">{numberInput("quantity", line.quantity, "1")}</td>
      <td className="px-3 py-3 text-right">{numberInput("unit_price", line.unit_price, "0.01")}</td>
      <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">{money(line.extended)}</td>
      <td className="px-2 py-3">
        {editable && (
          <button
            title="Remove line"
            onClick={() => run(() => api.deleteLine(rfqId, line.id))}
            className="rounded p-1 text-zinc-400 hover:bg-red-50 hover:text-red-600"
          >
            ✕
          </button>
        )}
      </td>
    </tr>
  );
}

function OutcomePanel({ rfq, busy, run }: { rfq: RfqDetail; busy: boolean; run: Run }) {
  const failed = rfq.deliveries.some((d) => d.status === "failed");
  return (
    <Card
      title="Quote reply & export"
      action={
        rfq.gmail_draft_id ? (
          <a
            href="https://mail.google.com/mail/u/0/#drafts"
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-indigo-700 hover:underline"
          >
            Draft saved in Gmail ↗
          </a>
        ) : null
      }
    >
      {rfq.draft_reply && (
        <pre className="max-h-80 overflow-auto whitespace-pre border-b border-zinc-100 bg-zinc-50/60 px-4 py-3 font-mono text-xs leading-relaxed text-zinc-800">
          {rfq.draft_reply}
        </pre>
      )}
      <div className="space-y-2 px-4 py-3">
        {rfq.deliveries.length === 0 && rfq.status === "approved" && (
          <div className="text-sm text-zinc-500">Sending signed export…</div>
        )}
        {rfq.deliveries.map((d) => (
          <div key={d.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div className="min-w-0">
              <span className="font-mono text-xs">{d.event}</span>
              <span className="text-zinc-400"> → </span>
              <span className="break-all font-mono text-xs text-zinc-600">{d.url}</span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span
                className={`rounded px-1.5 py-0.5 font-medium ${
                  d.status === "delivered" ? "bg-emerald-50 text-emerald-700" : d.status === "failed" ? "bg-red-50 text-red-700" : "bg-zinc-100 text-zinc-600"
                }`}
              >
                {d.status}
                {d.last_status_code ? ` · HTTP ${d.last_status_code}` : ""}
              </span>
              <span className="text-zinc-400">
                {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
              </span>
            </div>
            {d.last_error && d.status !== "delivered" && (
              <div className="w-full truncate font-mono text-[11px] text-red-600">{d.last_error}</div>
            )}
          </div>
        ))}
        {failed && rfq.status === "approved" && (
          <Button disabled={busy} onClick={() => run(() => api.retryExport(rfq.id))}>
            Retry export
          </Button>
        )}
      </div>
    </Card>
  );
}

function AuditPanel({ rfq }: { rfq: RfqDetail }) {
  return (
    <Card title="Audit trail">
      <ol className="space-y-0 px-4 py-3">
        {rfq.audit.map((e) => {
          const kind = e.actor.startsWith("ai:") ? "ai" : e.actor.startsWith("user:") ? "user" : "system";
          const dot = { ai: "bg-violet-500", user: "bg-indigo-500", system: "bg-zinc-400" }[kind];
          return (
            <li key={e.id} className="relative border-l border-zinc-200 pb-3 pl-4 last:pb-0">
              <span className={`absolute -left-[4.5px] top-1.5 size-2 rounded-full ${dot}`} />
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-mono text-xs font-medium">{e.action}</span>
                <span className="text-[11px] text-zinc-400" title={e.created_at}>
                  {e.actor} · {timeAgo(e.created_at)}
                </span>
              </div>
              {Object.keys(e.detail).length > 0 && (
                <details className="mt-0.5">
                  <summary className="cursor-pointer text-[11px] text-zinc-500">details</summary>
                  <pre className="mt-1 overflow-x-auto rounded bg-zinc-50 p-2 font-mono text-[11px] text-zinc-700">
                    {JSON.stringify(e.detail, null, 2)}
                  </pre>
                </details>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
