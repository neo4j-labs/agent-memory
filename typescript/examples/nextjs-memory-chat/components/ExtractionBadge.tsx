"use client";

/**
 * The badge that makes asynchronous extraction legible.
 *
 * NAMS writes a message immediately and extracts its entities afterwards, so
 * there is a window in which the transcript is ahead of the graph. Rather than
 * hiding that behind a `setTimeout`, the rail asks the service to tell it when
 * the window closes (`longTerm.waitForExtraction`) and this badge shows which
 * state we are in.
 */

import { Badge, HStack, Spinner, Text } from "@chakra-ui/react";

export type ExtractionState = "idle" | "waiting" | "settled" | "quiet";

const LABELS: Record<ExtractionState, { text: string; palette: string }> = {
  idle: { text: "idle", palette: "gray" },
  waiting: { text: "extracting…", palette: "orange" },
  settled: { text: "new entities", palette: "green" },
  quiet: { text: "nothing new", palette: "gray" },
};

export function ExtractionBadge({
  state,
  entityCount,
}: {
  state: ExtractionState;
  entityCount: number | null;
}) {
  const label = LABELS[state];
  return (
    <HStack gap={2}>
      <Badge colorPalette={label.palette} variant="subtle">
        {state === "waiting" && <Spinner size="xs" mr={1} />}
        {label.text}
      </Badge>
      {entityCount !== null && (
        <Text fontSize="xs" color="fg.muted">
          {entityCount} entit{entityCount === 1 ? "y" : "ies"} matched the last turn
        </Text>
      )}
    </HStack>
  );
}
