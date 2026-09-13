"use client";

import { useRef, useState } from "react";
import { api, type Candidate } from "@/lib/api";

export default function ProductPicker({
  candidates,
  onPick,
  label,
  disabled,
}: {
  candidates: Candidate[];
  onPick: (productId: number) => void;
  label: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Candidate[] | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  function search(q: string) {
    setQuery(q);
    if (debounce.current) clearTimeout(debounce.current);
    if (!q.trim()) {
      setResults(null);
      return;
    }
    debounce.current = setTimeout(() => {
      api.searchProducts(q).then(setResults).catch(() => setResults([]));
    }, 250);
  }

  function pick(id: number) {
    setOpen(false);
    setQuery("");
    setResults(null);
    onPick(id);
  }

  const list = results ?? candidates;

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="text-xs font-medium text-indigo-700 hover:underline disabled:text-zinc-300 disabled:no-underline"
      >
        {label}
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-1 w-96 rounded-lg border border-zinc-200 bg-white p-2 shadow-lg">
          <input
            autoFocus
            value={query}
            onChange={(e) => search(e.target.value)}
            placeholder="Search catalog by SKU or name…"
            className="w-full rounded-md border border-zinc-300 px-2 py-1.5 text-sm outline-none focus:border-zinc-500"
          />
          <div className="mt-1 text-[11px] uppercase tracking-wide text-zinc-400">
            {results ? "Search results" : "Suggested by matcher"}
          </div>
          <ul className="mt-1 max-h-64 overflow-y-auto">
            {list.map((c) => (
              <li key={c.product_id}>
                <button
                  type="button"
                  onClick={() => pick(c.product_id)}
                  className="flex w-full items-start justify-between gap-3 rounded px-2 py-1.5 text-left hover:bg-zinc-50"
                >
                  <span className="min-w-0">
                    <span className="block font-mono text-xs font-medium">{c.sku}</span>
                    <span className="block truncate text-xs text-zinc-500">{c.name}</span>
                  </span>
                  <span className="shrink-0 text-right text-xs">
                    <span className="block tabular-nums">${c.unit_price}</span>
                    <span className="block font-mono text-[10px] text-zinc-400">score {c.score.toFixed(2)}</span>
                  </span>
                </button>
              </li>
            ))}
            {list.length === 0 && <li className="px-2 py-3 text-xs text-zinc-500">No products found.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
