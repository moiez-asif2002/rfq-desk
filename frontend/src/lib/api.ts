export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type RfqStatus =
  | "processing"
  | "needs_review"
  | "ready"
  | "approved"
  | "exported"
  | "rejected"
  | "failed";

export interface Flag {
  code: string;
  message: string;
  severity?: string;
  field?: string;
  line_no?: number;
  evidence?: string[];
}

export interface Candidate {
  product_id: number;
  sku: string;
  name: string;
  unit_price: string;
  uom: string;
  stock_qty: number;
  score: number;
}

export interface Product {
  id: number;
  sku: string;
  name: string;
  uom: string;
  stock_qty: number;
  lead_time_days: number;
  list_price: number;
}

export interface Line {
  id: number;
  line_no: number;
  raw_part_number: string | null;
  raw_description: string;
  quantity: number | null;
  uom: string | null;
  confidence: number | null;
  match_status: "matched" | "ambiguous" | "unmatched" | "manual";
  match_score: number | null;
  match_method: "exact_sku" | "trigram" | "ai_rerank" | "manual" | null;
  match_reason: string | null;
  candidates: Candidate[];
  unit_price: number | null;
  extended: number | null;
  product: Product | null;
}

export interface RfqSummary {
  id: number;
  quote_number: string;
  status: RfqStatus;
  source: "webhook" | "gmail" | "upload";
  subject: string;
  from_email: string;
  from_name: string | null;
  received_at: string;
  customer_name: string | null;
  customer_company: string | null;
  line_count: number;
  total: number;
  flag_codes: string[];
  assigned_rep: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface Delivery {
  id: number;
  event: string;
  url: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
}

export interface RfqDetail extends RfqSummary {
  customer_email: string | null;
  customer_phone: string | null;
  requested_delivery_date: string | null;
  ship_to: string | null;
  notes: string | null;
  header_confidence: number | null;
  review_flags: Flag[];
  extraction_model: string | null;
  prompt_version: string | null;
  extraction_ms: number | null;
  draft_reply: string | null;
  gmail_draft_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  assigned_rep_email: string | null;
  message: {
    body_text: string;
    attachments: { id: number; filename: string; content_type: string; size_bytes: number }[];
  };
  lines: Line[];
  approval_problems: string[];
  audit: AuditEntry[];
  deliveries: Delivery[];
}

export interface GmailStatus {
  connected: boolean;
  email: string | null;
  last_synced_at: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(ApiError.describe(detail));
  }

  static describe(detail: unknown): string {
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
    return "Request failed";
  }

  get problems(): string[] {
    const d = this.detail;
    if (d && typeof d === "object" && "problems" in d && Array.isArray(d.problems)) return d.problems;
    return [];
  }
}

const REVIEWER = "reviewer@acme-industrial.example";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      "X-User": REVIEWER,
      ...init?.headers,
    },
    cache: "no-store",
  });
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, typeof body === "object" && body ? body.detail : body);
  return body as T;
}

const json = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) });

export const api = {
  list: (status?: string) =>
    request<{ items: RfqSummary[]; counts: Partial<Record<RfqStatus, number>> }>(
      `/api/rfqs${status ? `?status=${status}` : ""}`,
    ),
  get: (id: number) => request<RfqDetail>(`/api/rfqs/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ rfq_id: number; duplicate: boolean }>("/api/rfqs/upload", { method: "POST", body: form });
  },
  update: (id: number, data: Partial<RfqDetail>) => request<RfqDetail>(`/api/rfqs/${id}`, json("PATCH", data)),
  updateLine: (id: number, lineId: number, data: { product_id?: number | null; quantity?: number; unit_price?: number }) =>
    request<RfqDetail>(`/api/rfqs/${id}/lines/${lineId}`, json("PATCH", data)),
  deleteLine: (id: number, lineId: number) =>
    request<RfqDetail>(`/api/rfqs/${id}/lines/${lineId}`, { method: "DELETE" }),
  approve: (id: number) => request<RfqDetail>(`/api/rfqs/${id}/approve`, json("POST", { approver: REVIEWER })),
  reject: (id: number, reason: string) => request<RfqDetail>(`/api/rfqs/${id}/reject`, json("POST", { reason })),
  reprocess: (id: number) => request<unknown>(`/api/rfqs/${id}/reprocess`, { method: "POST" }),
  retryExport: (id: number) => request<unknown>(`/api/rfqs/${id}/retry-export`, { method: "POST" }),
  searchProducts: (q: string) => request<Candidate[]>(`/api/products?q=${encodeURIComponent(q)}`),
  gmailStatus: () => request<GmailStatus>("/api/gmail/status"),
  gmailSync: () => request<{ created: number[] }>("/api/gmail/sync", { method: "POST" }),
};

export const attachmentUrl = (id: number) => `${API_URL}/api/attachments/${id}`;
export const gmailConnectUrl = `${API_URL}/api/gmail/connect`;

export const money = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export function timeAgo(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
