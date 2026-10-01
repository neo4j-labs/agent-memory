/**
 * Node labels -> display label, colour and Chakra palette.
 *
 * Entity nodes carry `:Entity`, a POLE+O label (`:Person`, `:Event`, ...) and,
 * on bolt with an active ontology, the ontology's own label (`:Customer`,
 * `:Ticket`, and `:SupportCase` after the rename migration). The ontology
 * label is the interesting one, so it wins.
 *
 * NVL draws on a canvas and needs literal colours; the chrome around it uses
 * the Chakra palette names returned by `labelPalette`.
 */

export const POLE_LABELS = new Set([
  "Person",
  "Object",
  "Location",
  "Event",
  "Organization",
]);

const NON_DOMAIN_LABELS = new Set(["Entity", ...POLE_LABELS]);

/** Canvas colours (the Neo4j Bloom / NVL palette). */
const LABEL_COLORS: Record<string, string> = {
  Conversation: "#4C8EDA",
  Message: "#57C7E3",
  Customer: "#C990C0",
  Order: "#FFC454",
  Product: "#68BDF6",
  Ticket: "#F16667",
  SupportCase: "#F79767",
  Person: "#DE9BF9",
  Organization: "#FB95AF",
  Location: "#8DCC93",
  Event: "#D9C8AE",
  Object: "#A5ABB6",
  Entity: "#A5ABB6",
};

/** Colours for labels the app does not know (a custom ontology). */
const FALLBACK_COLORS = [
  "#6DCE9E",
  "#FF928C",
  "#ECB5C9",
  "#4C8EDA",
  "#DA7194",
  "#569480",
  "#FFE081",
  "#8DCC93",
];

const LABEL_PALETTES: Record<string, string> = {
  Customer: "purple",
  Order: "yellow",
  Product: "blue",
  Ticket: "red",
  SupportCase: "orange",
  Person: "purple",
  Organization: "pink",
  Location: "green",
  Event: "orange",
  Object: "gray",
  Conversation: "blue",
  Message: "cyan",
};

function hash(text: string): number {
  let value = 0;
  for (let i = 0; i < text.length; i += 1) {
    value = (value * 31 + text.charCodeAt(i)) | 0;
  }
  return Math.abs(value);
}

export function labelColor(label: string): string {
  return (
    LABEL_COLORS[label] ?? FALLBACK_COLORS[hash(label) % FALLBACK_COLORS.length]
  );
}

export function labelPalette(label: string): string {
  return LABEL_PALETTES[label] ?? "gray";
}

/** `PERSON` -> `Person`, `SUPPORT_CASE` -> `SupportCase`. */
export function pascalCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join("");
}

/**
 * The most specific label of an entity: an ontology label when present,
 * then the POLE+O label, then the `type` property.
 *
 * @param preferred labels to try first (the active ontology's labels).
 */
export function primaryLabel(
  labels: string[],
  type?: string | null,
  preferred?: readonly string[],
): string {
  if (preferred) {
    const hit = preferred.find((label) => labels.includes(label));
    if (hit) return hit;
  }
  const domain = labels.filter((label) => !NON_DOMAIN_LABELS.has(label));
  if (domain.length > 0) return [...domain].sort()[0];
  const pole = labels.find((label) => POLE_LABELS.has(label));
  if (pole) return pole;
  if (type) return pascalCase(type);
  return labels[0] ?? "Entity";
}

/** `PERSON` + `CUSTOMER` -> `PERSON:CUSTOMER`. */
export function fullType(type: string, subtype?: string | null): string {
  return subtype ? `${type}:${subtype}` : type;
}
