import type { RfqStatus } from "@/lib/api";

const STATUS_STYLES: Record<RfqStatus, { label: string; className: string }> = {
  processing: { label: "Processing", className: "bg-sky-50 text-sky-700 ring-sky-600/20" },
  needs_review: { label: "Needs review", className: "bg-amber-50 text-amber-800 ring-amber-600/25" },
  ready: { label: "Ready", className: "bg-emerald-50 text-emerald-700 ring-emerald-600/20" },
  approved: { label: "Approved", className: "bg-indigo-50 text-indigo-700 ring-indigo-600/20" },
  exported: { label: "Exported", className: "bg-violet-50 text-violet-700 ring-violet-600/20" },
  rejected: { label: "Rejected", className: "bg-zinc-100 text-zinc-600 ring-zinc-500/20" },
  failed: { label: "Failed", className: "bg-red-50 text-red-700 ring-red-600/20" },
};

export function StatusBadge({ status }: { status: RfqStatus }) {
  const s = STATUS_STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${s.className}`}>
      {status === "processing" && <span className="size-1.5 animate-pulse rounded-full bg-sky-500" />}
      {s.label}
    </span>
  );
}

const FLAG_LABELS: Record<string, string> = {
  prompt_injection: "Injection attempt",
  missing_field: "Missing info",
  sender_mismatch: "Sender mismatch",
  low_confidence: "Low confidence",
  ambiguous_match: "Ambiguous match",
  unmatched: "No match",
  stock_short: "Stock short",
  no_lines: "No lines",
  not_rfq: "Not an RFQ",
};

export function FlagChip({ code }: { code: string }) {
  const danger = code === "prompt_injection";
  const info = code === "stock_short" || code === "not_rfq";
  return (
    <span
      className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${
        danger ? "bg-red-100 text-red-800" : info ? "bg-zinc-100 text-zinc-600" : "bg-amber-100 text-amber-900"
      }`}
    >
      {FLAG_LABELS[code] ?? code}
    </span>
  );
}

export function Card({ title, action, children, className = "" }: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-zinc-200 bg-white shadow-sm ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-zinc-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-zinc-800">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-zinc-900 text-white hover:bg-zinc-700 disabled:bg-zinc-300",
    secondary: "bg-white text-zinc-800 ring-1 ring-inset ring-zinc-300 hover:bg-zinc-50 disabled:text-zinc-400",
    danger: "bg-white text-red-700 ring-1 ring-inset ring-red-200 hover:bg-red-50 disabled:text-red-300",
    ghost: "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 disabled:text-zinc-300",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed ${styles} ${className}`}
    />
  );
}

export function Confidence({ value }: { value: number | null }) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const color = value >= 0.8 ? "text-emerald-700" : value >= 0.6 ? "text-amber-700" : "text-red-700";
  return <span className={`font-mono text-[11px] ${color}`}>{pct}%</span>;
}
