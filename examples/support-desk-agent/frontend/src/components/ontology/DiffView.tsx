"use client";

import { Badge, Box, Flex, Stack, Text } from "@chakra-ui/react";
import { preview } from "@/lib/format";
import type { DiffSection, OntologyDiff } from "@/lib/types";

type Kind = keyof DiffSection;

const KIND_META: Record<Kind, { sign: string; palette: string; label: string }> =
  {
    added: { sign: "+", palette: "green", label: "added" },
    removed: { sign: "−", palette: "red", label: "removed" },
    renamed: { sign: "↻", palette: "blue", label: "renamed" },
    modified: { sign: "~", palette: "yellow", label: "modified" },
  };

const KINDS: Kind[] = ["added", "removed", "renamed", "modified"];

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value ? value : null;
}

/** One line describing a diff entry, whatever shape the backend sent. */
export function describeDiffItem(item: unknown): string {
  const record = asRecord(item);
  if (!record) return preview(item, 120);

  // Renames: {from, to} or {old, new} (labels or nested definitions).
  const from = record.from ?? record.old;
  const to = record.to ?? record.new;
  if (from !== undefined && to !== undefined) {
    const name = (value: unknown) => {
      const nested = asRecord(value);
      return nested
        ? (str(nested, "label") ?? str(nested, "type") ?? preview(value, 40))
        : String(value);
    };
    return `${name(from)} → ${name(to)}`;
  }

  // Relationship definitions: (Source)-[:TYPE]->(Target).
  const type = str(record, "type");
  const source = str(record, "source");
  const target = str(record, "target");
  if (type && source && target) return `(${source})-[:${type}]->(${target})`;

  // Entity type definitions: Label (POLE:SUBTYPE).
  const label = str(record, "label");
  if (label) {
    const pole = str(record, "pole_type");
    const subtype = str(record, "subtype");
    return pole ? `${label} (${pole}${subtype ? `:${subtype}` : ""})` : label;
  }
  return preview(item, 120);
}

function Changes({ item }: { item: unknown }) {
  const changes = asRecord(asRecord(item)?.changes);
  if (!changes) return null;
  return (
    <Stack gap="0.5" pl="5" mt="0.5">
      {Object.entries(changes).map(([field, change]) => {
        const pair = asRecord(change);
        return (
          <Text key={field} fontSize="xs" color="fg.muted">
            <Text as="span" fontFamily="mono">
              {field}
            </Text>
            :{" "}
            {pair
              ? `${preview(pair.from, 60)} → ${preview(pair.to, 60)}`
              : preview(change, 120)}
          </Text>
        );
      })}
    </Stack>
  );
}

function SectionView({ title, section }: { title: string; section: DiffSection }) {
  const total = KINDS.reduce((n, kind) => n + (section?.[kind]?.length ?? 0), 0);
  return (
    <Box>
      <Text fontSize="xs" fontWeight="semibold" mb="1">
        {title}
      </Text>
      {total === 0 ? (
        <Text fontSize="xs" color="fg.muted">
          No changes.
        </Text>
      ) : (
        <Stack gap="1">
          {KINDS.flatMap((kind) =>
            (section?.[kind] ?? []).map((item, index) => (
              <Box key={`${kind}-${index}`}>
                <Flex gap="2" alignItems="center">
                  <Badge
                    size="xs"
                    colorPalette={KIND_META[kind].palette}
                    minW="5"
                    justifyContent="center"
                    title={KIND_META[kind].label}
                  >
                    {KIND_META[kind].sign}
                  </Badge>
                  <Text fontSize="xs" fontFamily="mono">
                    {describeDiffItem(item)}
                  </Text>
                </Flex>
                {kind === "modified" ? <Changes item={item} /> : null}
              </Box>
            )),
          )}
        </Stack>
      )}
    </Box>
  );
}

/** Structural diff between two revisions, as `ontology.diff()` reports it. */
export function DiffView({ diff }: { diff: OntologyDiff }) {
  return (
    <Stack
      gap="3"
      p="3"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      bg="bg.subtle"
    >
      <Flex alignItems="center" gap="2" flexWrap="wrap">
        <Text fontSize="sm" fontWeight="medium">
          Revision {diff.from_revision} → {diff.to_revision}
        </Text>
        {diff.mode_change ? (
          <Badge colorPalette="orange" size="sm">
            mode: {diff.mode_change.from} → {diff.mode_change.to}
          </Badge>
        ) : (
          <Badge variant="outline" size="sm">
            mode unchanged
          </Badge>
        )}
      </Flex>
      <SectionView title="Entity types" section={diff.entity_types} />
      <SectionView title="Relationships" section={diff.relationships} />
      <Text fontSize="xs" color="fg.muted">
        Renames cannot be inferred structurally, so the library reports a
        rename as one removal plus one addition.
      </Text>
    </Stack>
  );
}
