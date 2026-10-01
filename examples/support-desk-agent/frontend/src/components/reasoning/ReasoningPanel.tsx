"use client";

import {
  Badge,
  Box,
  Flex,
  IconButton,
  Spinner,
  Stack,
  Table,
  Text,
  Timeline,
} from "@chakra-ui/react";
import { useCallback, useMemo } from "react";
import {
  LuChartBar,
  LuCheck,
  LuHistory,
  LuLoader,
  LuRefreshCw,
  LuRoute,
  LuSearch,
  LuX,
} from "react-icons/lu";
import { EmptyRow, LoadingRow, PanelSection } from "@/components/ui/PanelSection";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatDateTime, formatDuration, formatRate } from "@/lib/format";
import type { TraceSummary } from "@/lib/types";
import { OutcomeBadge, SeededBadge } from "./badges";
import { TraceDetailView } from "./TraceDetailView";

interface ReasoningPanelProps {
  threadId: string | null;
  /** Bumped when a turn starts or finishes. */
  refreshKey: number;
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string | null) => void;
}

function indicatorFor(trace: TraceSummary) {
  if (trace.success === true) {
    return { palette: "green", icon: <LuCheck /> };
  }
  if (trace.success === false) {
    return { palette: "red", icon: <LuX /> };
  }
  return { palette: "yellow", icon: <LuLoader /> };
}

function TraceTimeline({
  traces,
  selectedTraceId,
  onSelect,
}: {
  traces: TraceSummary[];
  selectedTraceId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Timeline.Root size="sm" variant="subtle">
      {traces.map((trace) => {
        const indicator = indicatorFor(trace);
        const selected = trace.id === selectedTraceId;
        return (
          <Timeline.Item key={trace.id}>
            <Timeline.Connector>
              <Timeline.Separator />
              <Timeline.Indicator colorPalette={indicator.palette}>
                {indicator.icon}
              </Timeline.Indicator>
            </Timeline.Connector>
            <Timeline.Content pb="3" minW="0">
              <Box
                as="button"
                textAlign="left"
                w="full"
                p="2"
                mt="-1"
                borderRadius="md"
                bg={selected ? "brand.subtle" : "transparent"}
                borderWidth="1px"
                borderColor={selected ? "brand.emphasized" : "transparent"}
                _hover={{ bg: selected ? "brand.subtle" : "bg.muted" }}
                onClick={() => onSelect(trace.id)}
                aria-pressed={selected}
              >
                <Timeline.Title fontSize="sm" lineClamp={2}>
                  {trace.task}
                </Timeline.Title>
                <Flex gap="1" mt="1" flexWrap="wrap" alignItems="center">
                  <OutcomeBadge success={trace.success} />
                  {trace.seeded ? <SeededBadge /> : null}
                  <Badge size="sm" variant="outline">
                    {trace.step_count} step{trace.step_count === 1 ? "" : "s"}
                  </Badge>
                  <Badge size="sm" variant="outline">
                    {trace.tool_call_count} tool call
                    {trace.tool_call_count === 1 ? "" : "s"}
                  </Badge>
                </Flex>
                {trace.outcome_summary ? (
                  <Timeline.Description mt="1" lineClamp={2}>
                    {trace.outcome_summary}
                  </Timeline.Description>
                ) : null}
                <Text fontSize="xs" color="fg.subtle" mt="1">
                  {formatDateTime(trace.started_at)}
                </Text>
              </Box>
            </Timeline.Content>
          </Timeline.Item>
        );
      })}
    </Timeline.Root>
  );
}

function SimilarTasks({
  task,
  excludeId,
  onSelect,
}: {
  task: string;
  excludeId: string;
  onSelect: (id: string) => void;
}) {
  const load = useCallback(
    (signal: AbortSignal) => api.traces.similar(task, 5, { signal }),
    [task],
  );
  const { data, error, loading } = useApi(load);
  const similar = (data ?? []).filter((trace) => trace.id !== excludeId);

  return (
    <Box
      p="3"
      borderRadius="md"
      borderWidth="1px"
      borderColor="border.subtle"
      bg="bg.subtle"
    >
      <Flex alignItems="center" gap="2" mb="2">
        <LuSearch size={14} />
        <Text fontSize="sm" fontWeight="semibold" flex="1">
          Similar past tasks
        </Text>
        {loading ? <Spinner size="xs" /> : null}
      </Flex>
      <Text fontSize="xs" color="fg.muted" mb="2">
        What <code>recall_similar_tasks</code> would retrieve for this task from
        reasoning memory (embedding similarity over earlier traces).
      </Text>
      {error ? (
        <Text fontSize="xs" color="fg.error">
          {error}
        </Text>
      ) : data && similar.length === 0 ? (
        <Text fontSize="xs" color="fg.muted">
          No similar traces.
        </Text>
      ) : (
        <Stack gap="1">
          {similar.map((trace) => (
            <Box
              key={trace.id}
              as="button"
              textAlign="left"
              p="2"
              borderRadius="md"
              bg="bg.panel"
              _hover={{ bg: "bg.muted" }}
              onClick={() => onSelect(trace.id)}
            >
              <Flex gap="1" alignItems="center" flexWrap="wrap">
                <OutcomeBadge success={trace.success} />
                {trace.seeded ? <SeededBadge /> : null}
              </Flex>
              <Text fontSize="sm" mt="1" lineClamp={2}>
                {trace.task}
              </Text>
              {trace.outcome_summary ? (
                <Text fontSize="xs" color="fg.muted" lineClamp={2}>
                  {trace.outcome_summary}
                </Text>
              ) : null}
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
}

function ToolStatsTable({ refreshKey }: { refreshKey: number }) {
  const load = useCallback(
    (signal: AbortSignal) => api.toolStats({ signal }),
    [],
  );
  const { data, error, loading } = useApi(load, refreshKey);

  if (error) {
    return <StatusAlert status="error" title="Tool stats unavailable" description={error} />;
  }
  if (!data) return loading ? <LoadingRow /> : null;
  if (data.length === 0) return <EmptyRow>No tool calls recorded yet.</EmptyRow>;

  const rows = [...data].sort((a, b) => b.calls - a.calls);
  return (
    <Table.ScrollArea borderWidth="1px" borderRadius="md">
      <Table.Root size="sm">
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>Tool</Table.ColumnHeader>
            <Table.ColumnHeader textAlign="end">Calls</Table.ColumnHeader>
            <Table.ColumnHeader textAlign="end">Success</Table.ColumnHeader>
            <Table.ColumnHeader textAlign="end">Avg</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {rows.map((row) => (
            <Table.Row key={row.name}>
              <Table.Cell fontFamily="mono" fontSize="xs">
                {row.name}
              </Table.Cell>
              <Table.Cell textAlign="end">{row.calls}</Table.Cell>
              <Table.Cell textAlign="end">{formatRate(row.success_rate)}</Table.Cell>
              <Table.Cell textAlign="end" whiteSpace="nowrap">
                {formatDuration(row.avg_duration_ms)}
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Table.ScrollArea>
  );
}

/**
 * Reasoning memory: the thread's traces as a timeline, the selected trace's
 * steps and tool calls, similar past tasks, and aggregate tool statistics.
 */
export function ReasoningPanel({
  threadId,
  refreshKey,
  selectedTraceId,
  onSelectTrace,
}: ReasoningPanelProps) {
  const loadTraces = useCallback(
    (signal: AbortSignal) => api.traces.list(threadId, { signal }),
    [threadId],
  );
  const traces = useApi(loadTraces, refreshKey);

  const loadDetail = useMemo(
    () =>
      selectedTraceId
        ? (signal: AbortSignal) => api.traces.get(selectedTraceId, { signal })
        : null,
    [selectedTraceId],
  );
  const detail = useApi(loadDetail, refreshKey);

  const list = traces.data ?? [];

  return (
    <Stack gap="6">
      <PanelSection
        title={threadId ? "Traces in this conversation" : "All traces"}
        icon={<LuHistory size={16} />}
        description="Each agent turn is a ReasoningTrace: one step per tool call, with TOUCHED edges to the entities it read."
        actions={
          <IconButton
            aria-label="Refresh traces"
            size="xs"
            variant="ghost"
            onClick={traces.reload}
            disabled={traces.loading}
          >
            {traces.loading ? <Spinner size="xs" /> : <LuRefreshCw />}
          </IconButton>
        }
      >
        {traces.error ? (
          <StatusAlert
            status="error"
            title="Traces unavailable"
            description={traces.error}
          />
        ) : null}
        {!traces.data && traces.loading ? (
          <LoadingRow label="Loading traces…" />
        ) : traces.data && list.length === 0 ? (
          <EmptyRow>
            No traces {threadId ? "for this conversation" : ""} yet — every
            chat turn records one.
          </EmptyRow>
        ) : (
          <TraceTimeline
            traces={list}
            selectedTraceId={selectedTraceId}
            onSelect={onSelectTrace}
          />
        )}
      </PanelSection>

      <PanelSection
        title="Trace detail"
        icon={<LuRoute size={16} />}
        actions={
          detail.loading && selectedTraceId ? <Spinner size="xs" /> : null
        }
      >
        {!selectedTraceId ? (
          <EmptyRow>
            Select a trace above, or click a trace chip on an assistant reply.
          </EmptyRow>
        ) : detail.error ? (
          <StatusAlert
            status="error"
            title="Trace unavailable"
            description={detail.error}
          />
        ) : detail.data ? (
          <Stack gap="4">
            <TraceDetailView trace={detail.data} />
            <SimilarTasks
              task={detail.data.task}
              excludeId={detail.data.id}
              onSelect={onSelectTrace}
            />
          </Stack>
        ) : (
          <LoadingRow label="Loading trace…" />
        )}
      </PanelSection>

      <PanelSection
        title="Tool stats"
        icon={<LuChartBar size={16} />}
        description="Aggregated over every recorded tool call in the database."
      >
        <ToolStatsTable refreshKey={refreshKey} />
      </PanelSection>
    </Stack>
  );
}
