import {
  Box,
  Text,
  Badge,
  VStack,
  HStack,
  Card,
  Heading,
  Separator,
  Collapsible,
} from '@chakra-ui/react'
import { motion, AnimatePresence } from 'motion/react'
import {
  LuBot,
  LuFileCheck,
  LuSearch,
  LuUsers,
  LuShield,
  LuChevronDown,
  LuChevronUp,
  LuDatabase,
  LuBrain,
} from 'react-icons/lu'
import { useState } from 'react'
import type { AgentState } from '../../hooks/useAgentStream'
import { AGENT_DESCRIPTIONS, agentColor, agentLabel } from '../../lib/agents'
import { ToolCallCard } from './ToolCallCard'
import { MemoryAccessIndicator } from './MemoryAccessIndicator'

interface AgentOrchestrationViewProps {
  agentStates: Map<string, AgentState>
  activeAgent: string | null
  delegationChain: Array<{ from: string; to: string }>
  isStreaming: boolean
  traceId?: string | null
  totalDurationMs?: number
}

const agentIcons: Record<string, React.ReactNode> = {
  supervisor: <LuBot size={16} />,
  kyc_agent: <LuFileCheck size={16} />,
  aml_agent: <LuSearch size={16} />,
  relationship_agent: <LuUsers size={16} />,
  compliance_agent: <LuShield size={16} />,
}

function getAgentConfig(name: string) {
  return {
    label: agentLabel(name),
    icon: agentIcons[name] ?? <LuBot size={16} />,
    color: agentColor(name),
    description: AGENT_DESCRIPTIONS[name] ?? 'Processing',
  }
}

function ActiveDot() {
  return (
    <motion.div
      animate={{ scale: [1, 1.3, 1], opacity: [1, 0.7, 1] }}
      transition={{ repeat: Infinity, duration: 1.5, ease: 'easeInOut' }}
    >
      <Box w={2} h={2} borderRadius="full" bg="green.500" />
    </motion.div>
  )
}

function AgentCard({
  agentName,
  state,
  isActive,
}: {
  agentName: string
  state: AgentState
  isActive: boolean
}) {
  const config = getAgentConfig(agentName)
  const [expanded, setExpanded] = useState(true)

  const totalToolCalls = state.toolCalls.length
  const totalMemory = state.memoryAccesses.length

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      layout
    >
      <Card.Root
        size="sm"
        variant="outline"
        borderColor={isActive ? `${config.color}.300` : 'border.subtle'}
        bg={isActive ? `${config.color}.50` : 'bg.panel'}
        transition="all 0.2s"
      >
        <Card.Header py={2} px={3}>
          <HStack justify="space-between">
            <HStack gap={2}>
              <Box color={`${config.color}.500`}>{config.icon}</Box>
              <Text fontWeight="semibold" fontSize="sm">
                {config.label}
              </Text>
              {isActive && <ActiveDot />}
              {state.status === 'complete' && (
                <Badge size="sm" colorPalette="green" variant="subtle">
                  Complete
                </Badge>
              )}
            </HStack>
            <HStack gap={2}>
              {totalToolCalls > 0 && (
                <Badge size="sm" variant="outline" colorPalette={config.color}>
                  {totalToolCalls} tools
                </Badge>
              )}
              {totalMemory > 0 && (
                <Badge size="sm" variant="outline" colorPalette="blue">
                  <LuDatabase size={10} />
                  {totalMemory}
                </Badge>
              )}
              <Box
                cursor="pointer"
                onClick={() => setExpanded(!expanded)}
                color="fg.muted"
              >
                {expanded ? <LuChevronUp size={14} /> : <LuChevronDown size={14} />}
              </Box>
            </HStack>
          </HStack>
        </Card.Header>

        <Collapsible.Root open={expanded}>
          <Collapsible.Content>
            <Card.Body pt={0} px={3} pb={3}>
              <VStack gap={2} align="stretch">
                {/* Thoughts */}
                <AnimatePresence>
                  {state.thoughts.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.2 }}
                    >
                      <HStack gap={1} color="fg.muted" mb={1}>
                        <LuBrain size={12} />
                        <Text fontSize="xs" fontWeight="medium">Reasoning</Text>
                      </HStack>
                      {state.thoughts.slice(-2).map((thought, i) => (
                        <Text key={i} fontSize="xs" color="fg.muted" lineClamp={2}>
                          {thought}
                        </Text>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Memory accesses */}
                <AnimatePresence>
                  {state.memoryAccesses.map((ma, i) => (
                    <MemoryAccessIndicator
                      key={`mem-${i}`}
                      operation={ma.operation}
                      tool={ma.tool}
                      query={ma.query}
                    />
                  ))}
                </AnimatePresence>

                {/* Tool calls with staggered animation */}
                <AnimatePresence>
                  <motion.div
                    variants={{
                      show: { transition: { staggerChildren: 0.08 } },
                    }}
                    initial="hidden"
                    animate="show"
                  >
                    <VStack gap={1.5} align="stretch">
                      {state.toolCalls.map((tc, i) => (
                        <ToolCallCard
                          key={`tc-${i}`}
                          tool={tc.tool}
                          args={tc.args}
                          result={tc.result}
                          agent={agentName}
                        />
                      ))}
                    </VStack>
                  </motion.div>
                </AnimatePresence>
              </VStack>
            </Card.Body>
          </Collapsible.Content>
        </Collapsible.Root>
      </Card.Root>
    </motion.div>
  )
}

export function AgentOrchestrationView({
  agentStates,
  activeAgent,
  isStreaming,
  traceId,
  totalDurationMs,
}: AgentOrchestrationViewProps) {
  const [minimized, setMinimized] = useState(false)
  const agents = Array.from(agentStates.entries())

  if (agents.length === 0 && !isStreaming) return null

  // Count totals
  let totalTools = 0
  let totalMemory = 0
  for (const [, state] of agents) {
    totalTools += state.toolCalls.length
    totalMemory += state.memoryAccesses.length
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.3 }}
    >
      <Card.Root variant="outline" borderColor="blue.200" bg="blue.50/30">
        <Card.Header py={2} px={4}>
          <HStack justify="space-between">
            <HStack gap={2}>
              <Box color="blue.500">
                <LuBot size={16} />
              </Box>
              <Heading size="sm">Agent Orchestration</Heading>
              {isStreaming && (
                <Badge size="sm" colorPalette="green" variant="solid">
                  <ActiveDot /> Live
                </Badge>
              )}
            </HStack>
            <HStack gap={2}>
              {totalDurationMs && (
                <Text fontSize="xs" color="fg.muted">
                  {(totalDurationMs / 1000).toFixed(1)}s
                </Text>
              )}
              <Box
                cursor="pointer"
                onClick={() => setMinimized(!minimized)}
                color="fg.muted"
              >
                {minimized ? <LuChevronDown size={14} /> : <LuChevronUp size={14} />}
              </Box>
            </HStack>
          </HStack>
        </Card.Header>

        <Collapsible.Root open={!minimized}>
          <Collapsible.Content>
            <Card.Body pt={0} px={4} pb={3}>
              {/* Agent cards */}
              <VStack gap={2} align="stretch">
                <AnimatePresence mode="popLayout">
                  {agents.map(([name, state]) => (
                    <AgentCard
                      key={name}
                      agentName={name}
                      state={state}
                      isActive={name === activeAgent}
                    />
                  ))}
                </AnimatePresence>
              </VStack>

              {/* Summary footer */}
              {!isStreaming && agents.length > 0 && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.3 }}
                >
                  <Separator my={3} />
                  <HStack gap={4} flexWrap="wrap">
                    <HStack gap={1}>
                      <LuDatabase size={12} />
                      <Text fontSize="xs" color="fg.muted">
                        {totalMemory} memory operations
                      </Text>
                    </HStack>
                    <Text fontSize="xs" color="fg.muted">
                      {totalTools} tool calls
                    </Text>
                    <Text fontSize="xs" color="fg.muted">
                      {agents.length} agents consulted
                    </Text>
                    {traceId && (
                      <Badge size="sm" variant="outline" colorPalette="purple">
                        Trace saved
                      </Badge>
                    )}
                  </HStack>
                </motion.div>
              )}
            </Card.Body>
          </Collapsible.Content>
        </Collapsible.Root>
      </Card.Root>
    </motion.div>
  )
}
