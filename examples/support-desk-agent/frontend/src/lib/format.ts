/** Small, pure formatting helpers shared by the panels. */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

/** `success_rate` may arrive as a 0..1 fraction or as a 0..100 percentage. */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return "—";
  const percent = rate <= 1 ? rate * 100 : rate;
  return `${Math.round(percent)}%`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value <= 1 ? `${Math.round(value * 100)}%` : String(value);
}

/** Pretty JSON for a tool argument/result; strings are shown as-is. */
export function toJson(value: unknown): string {
  if (typeof value === "string") {
    // Tool results are sometimes JSON-encoded strings; pretty-print those.
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** A one-line preview of an arbitrary value. */
export function preview(value: unknown, max = 160): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return truncate((text ?? "").replace(/\s+/g, " "), max);
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "—";
  return id.length > length ? id.slice(0, length) : id;
}
