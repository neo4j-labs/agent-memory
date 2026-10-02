"use client";

import {
  Badge,
  Box,
  Button,
  Collapsible,
  Flex,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuWrench } from "react-icons/lu";
import { EntityBadge } from "@/components/ui/EntityBadge";
import { JsonBlock } from "@/components/ui/JsonBlock";
import { formatDuration, preview, toJson } from "@/lib/format";
import { primaryLabel } from "@/lib/labels";
import type { TraceDetail, TraceStep, TraceToolCall } from "@/lib/types";
import { OutcomeBadge, SeededBadge } from "./badges";

function statusPalette(status: string): string {
  const s = status.toLowerCase();
  if (s === "success" || s === "ok" || s === "completed") return "green";
  if (s === "error" || s === "failure" || s === "failed" || s === "timeout")
    return "red";
  return "yellow";
}

function ToolCallView({ call }: { call: TraceToolCall }) {
  const full = toJson(call.result);
  const long = full.length > 280;
  return (
    <Box
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      p="2"
      bg="bg.panel"
    >
      <Flex alignItems="center" gap="2">
        <LuWrench size={12} />
        <Text fontSize="sm" fontFamily="mono" fontWeight="medium" flex="1">
          {call.tool_name}
        </Text>
        <Badge size="xs" colorPalette={statusPalette(call.status)}>
          {call.status}
        </Badge>
        <Text fontSize="xs" color="fg.muted">
          {formatDuration(call.duration_ms)}
        </Text>
      </Flex>
      <Text fontSize="xs" fontFamily="mono" color="fg.muted" mt="1">
        args {preview(call.arguments, 140)}
      </Text>
      <Collapsible.Root>
        <Text fontSize="xs" mt="1" whiteSpace="pre-wrap" wordBreak="break-word">
          <Text as="span" color="fg.muted">
            result{" "}
          </Text>
          {preview(call.result, 280)}
        </Text>
        {long ? (
          <>
            <Collapsible.Trigger asChild>
              <Button size="2xs" variant="plain" px="0" colorPalette="brand">
                Show full result
              </Button>
            </Collapsible.Trigger>
            <Collapsible.Content>
              <JsonBlock value={call.result} maxH="240px" />
            </Collapsible.Content>
          </>
        ) : null}
      </Collapsible.Root>
      {call.touched.length > 0 ? (
        <Flex gap="1" mt="2" flexWrap="wrap" alignItems="center">
          <Text fontSize="xs" color="fg.muted">
            TOUCHED
          </Text>
          {call.touched.map((entity) => (
            <EntityBadge
              key={entity.id}
              name={entity.name}
              label={primaryLabel(entity.labels)}
            />
          ))}
        </Flex>
      ) : null}
    </Box>
  );
}

function StepView({ step, index }: { step: TraceStep; index: number }) {
  return (
    <Box>
      <Flex alignItems="baseline" gap="2">
        <Badge size="xs" variant="solid" colorPalette="gray">
          {index + 1}
        </Badge>
        <Text fontSize="sm" fontWeight="medium">
          {step.action || step.thought || "Step"}
        </Text>
      </Flex>
      <Stack gap="1" pl="6" mt="1">
        {step.thought && step.action ? (
          <Text fontSize="xs" color="fg.muted">
            <b>Thought:</b> {step.thought}
          </Text>
        ) : null}
        {/* The chat route records observations from the tool result
            (auto_observation), so a step with a tool call would show the same
            JSON twice. The result below is the observation. */}
        {step.observation && step.tool_calls.length === 0 ? (
          <Text fontSize="xs" color="fg.muted">
            <b>Observation:</b> {preview(step.observation, 240)}
          </Text>
        ) : null}
        {step.tool_calls.map((call) => (
          <ToolCallView key={call.id} call={call} />
        ))}
      </Stack>
    </Box>
  );
}

/** One trace: outcome, metrics, then steps -> tool calls -> touched. */
export function TraceDetailView({ trace }: { trace: TraceDetail }) {
  const metrics = Object.entries(trace.metrics ?? {});
  return (
    <Stack gap="3">
      <Box>
        <Flex gap="2" alignItems="center" flexWrap="wrap">
          <OutcomeBadge success={trace.success} />
          {trace.seeded ? <SeededBadge /> : null}
          <Text fontSize="xs" color="fg.muted" fontFamily="mono">
            {trace.id}
          </Text>
        </Flex>
        <Text fontWeight="medium" mt="1">
          {trace.task}
        </Text>
        {trace.outcome_summary ? (
          <Text fontSize="sm" color="fg.muted" mt="1">
            {trace.outcome_summary}
          </Text>
        ) : null}
        {metrics.length > 0 ? (
          <Flex gap="1" mt="2" flexWrap="wrap">
            {metrics.map(([key, value]) => (
              <Badge key={key} size="xs" variant="outline">
                {key}: {preview(value, 40)}
              </Badge>
            ))}
          </Flex>
        ) : null}
      </Box>
      {trace.steps.length === 0 ? (
        <Text fontSize="sm" color="fg.muted">
          No steps recorded{trace.success === null ? " yet" : ""}.
        </Text>
      ) : (
        <Stack gap="3">
          {trace.steps.map((step, index) => (
            <StepView key={step.id} step={step} index={index} />
          ))}
        </Stack>
      )}
    </Stack>
  );
}
