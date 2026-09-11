import {
  Box,
  Text,
  Badge,
  HStack,
  VStack,
  Timeline,
  Code,
  Collapsible,
} from "@chakra-ui/react";
import { motion } from "motion/react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LuBot,
  LuFileCheck,
  LuSearch,
  LuUsers,
  LuShield,
  LuWrench,
  LuCheck,
  LuX,
  LuChevronDown,
  LuChevronRight,
  LuDatabase,
} from "react-icons/lu";
import type { AgentState } from "../../hooks/useAgentStream";
import { agentColor, agentLabel } from "../../lib/agents";
import { getTraceDetail, type ReasoningTrace } from "../../lib/api";

interface AgentActivityTimelineProps {
  agentStates: Map<string, AgentState>;
  agentsConsulted: string[];
  totalDurationMs?: number;
  traceId?: string | null;
}

const agentIcons: Record<string, React.ReactNode> = {
  supervisor: <LuBot size={14} />,
  kyc_agent: <LuFileCheck size={14} />,
  aml_agent: <LuSearch size={14} />,
  relationship_agent: <LuUsers size={14} />,
  compliance_agent: <LuShield size={14} />,
};

/** One timeline row, whichever source it came from. */
interface TimelineRow {
  key: string;
  /** Agent key (`supervisor`, `kyc_agent`, …) when we can infer one. */
  agent: string | null;
  title: string;
  status: string;
  toolCalls: Array<{ tool: string; ok: boolean }>;
  memoryOps: number;
}

/**
 * The backend records one reasoning step per agent activation, with the thought
 * `Agent <name> activated`, so the agent key can be recovered from a persisted
 * step and the row rendered exactly like the live one.
 */
function agentFromStep(thought: string | null, action: string | null): string | null {
  const match = /Agent ([a-z_]+) activated/.exec(thought ?? "");
  if (match) return match[1];
  const fromAction = /Processing as ([a-z_]+)/.exec(action ?? "");
  return fromAction ? fromAction[1] : null;
}

function rowsFromTrace(trace: ReasoningTrace): TimelineRow[] {
  return trace.steps.map((step) => {
    const agent = agentFromStep(step.thought, step.action);
    return {
      key: step.id,
      agent,
      title: agent
        ? agentLabel(agent)
        : (step.action ?? `Step ${step.step_number}`),
      status: "stored",
      toolCalls: step.tool_calls.map((tc) => ({
        tool: tc.tool_name,
        ok: tc.error === null && tc.status !== "error",
      })),
      memoryOps: 0,
    };
  });
}

function rowsFromLiveState(agentStates: Map<string, AgentState>): TimelineRow[] {
  return Array.from(agentStates.entries()).map(([name, state]) => ({
    key: name,
    agent: name,
    title: agentLabel(name),
    status: state.status === "complete" ? "Done" : state.status,
    toolCalls: state.toolCalls.map((tc) => ({
      tool: tc.tool,
      ok: tc.result !== undefined,
    })),
    memoryOps: state.memoryAccesses.length,
  }));
}

export function AgentActivityTimeline({
  agentStates,
  agentsConsulted,
  totalDurationMs,
  traceId,
}: AgentActivityTimelineProps) {
  const [expanded, setExpanded] = useState(false);

  // Prefer the trace that the backend persisted to Neo4j: it survives a reload,
  // whereas the live stream state only exists in this tab.
  const { data: trace } = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => getTraceDetail(traceId!),
    enabled: !!traceId,
    staleTime: Infinity,
  });

  const persisted = trace ? rowsFromTrace(trace) : [];
  const live = rowsFromLiveState(agentStates);
  const rows = persisted.length > 0 ? persisted : live;
  const fromNeo4j = persisted.length > 0;

  if (rows.length === 0 && agentsConsulted.length === 0) return null;

  const totalTools = rows.reduce((sum, row) => sum + row.toolCalls.length, 0);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <Box mt={2}>
        <HStack
          gap={2}
          cursor="pointer"
          onClick={() => setExpanded(!expanded)}
          _hover={{ color: "brand.fg" }}
          transition="color 0.15s"
        >
          <Box color="fg.muted">
            {expanded ? (
              <LuChevronDown size={12} />
            ) : (
              <LuChevronRight size={12} />
            )}
          </Box>
          <Text fontSize="xs" color="fg.muted" fontWeight="medium">
            Agent Activity
          </Text>
          <Badge size="sm" variant="outline">
            {agentsConsulted.length || rows.length} agents
          </Badge>
          {totalTools > 0 && (
            <Badge size="sm" variant="outline" colorPalette="blue">
              {totalTools} tools
            </Badge>
          )}
          {fromNeo4j && (
            <Badge size="sm" variant="subtle" colorPalette="teal">
              <LuDatabase size={10} /> loaded from Neo4j
            </Badge>
          )}
          {totalDurationMs && (
            <Text fontSize="xs" color="fg.muted">
              {(totalDurationMs / 1000).toFixed(1)}s
            </Text>
          )}
        </HStack>

        <Collapsible.Root open={expanded}>
          <Collapsible.Content>
            <Box mt={2} ml={2}>
              <Timeline.Root size="sm" variant="subtle">
                {rows.map((row) => {
                  const color = row.agent ? agentColor(row.agent) : "gray";
                  const icon =
                    (row.agent ? agentIcons[row.agent] : null) ?? (
                      <LuBot size={14} />
                    );

                  return (
                    <Timeline.Item key={row.key}>
                      <Timeline.Connector>
                        <Timeline.Separator />
                        <Timeline.Indicator
                          bg={`${color}.solid`}
                          color={`${color}.contrast`}
                        >
                          {icon}
                        </Timeline.Indicator>
                      </Timeline.Connector>
                      <Timeline.Content>
                        <Timeline.Title>
                          <HStack gap={2}>
                            <Text fontWeight="medium" fontSize="sm">
                              {row.title}
                            </Text>
                            <Badge
                              size="sm"
                              colorPalette={color}
                              variant="subtle"
                            >
                              {row.status}
                            </Badge>
                          </HStack>
                        </Timeline.Title>

                        {/* Tool calls summary */}
                        {row.toolCalls.length > 0 && (
                          <VStack gap={1} align="start" mt={1}>
                            {row.toolCalls.map((tc, i) => (
                              <HStack key={`${row.key}-${i}`} gap={1}>
                                <LuWrench size={10} />
                                <Code size="sm" variant="plain">
                                  {tc.tool}
                                </Code>
                                {tc.ok ? (
                                  <Box color="green.500">
                                    <LuCheck size={10} />
                                  </Box>
                                ) : (
                                  <Box color="red.500">
                                    <LuX size={10} />
                                  </Box>
                                )}
                              </HStack>
                            ))}
                          </VStack>
                        )}

                        {/* Memory accesses */}
                        {row.memoryOps > 0 && (
                          <HStack gap={1} mt={1}>
                            <Badge size="sm" variant="subtle" colorPalette="blue">
                              {row.memoryOps} Neo4j ops
                            </Badge>
                          </HStack>
                        )}
                      </Timeline.Content>
                    </Timeline.Item>
                  );
                })}

                {/* If no steps at all but we know who was consulted, list them */}
                {rows.length === 0 &&
                  agentsConsulted.map((name) => (
                    <Timeline.Item key={name}>
                      <Timeline.Connector>
                        <Timeline.Separator />
                        <Timeline.Indicator>
                          {agentIcons[name] ?? <LuBot size={14} />}
                        </Timeline.Indicator>
                      </Timeline.Connector>
                      <Timeline.Content>
                        <Timeline.Title>
                          {agentLabel(name)}
                        </Timeline.Title>
                      </Timeline.Content>
                    </Timeline.Item>
                  ))}
              </Timeline.Root>

              {trace?.outcome && (
                <Text fontSize="xs" color="fg.muted" mt={2} lineClamp={3}>
                  Outcome: {trace.outcome}
                </Text>
              )}
              {traceId && (
                <Text fontSize="xs" color="fg.muted" mt={1} fontFamily="mono">
                  (:ReasoningTrace {"{"}id: {traceId.slice(0, 8)}…{"}"})
                </Text>
              )}
            </Box>
          </Collapsible.Content>
        </Collapsible.Root>
      </Box>
    </motion.div>
  );
}
