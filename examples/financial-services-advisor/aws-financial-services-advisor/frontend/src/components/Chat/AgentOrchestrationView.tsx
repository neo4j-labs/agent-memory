import { Badge, Box, Card, Flex, HStack, Heading, Text, VStack } from '@chakra-ui/react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { LuBrain, LuChevronDown, LuChevronRight, LuDatabase } from 'react-icons/lu'
import type { AgentState } from '../../hooks/useAgentStream'
import MemoryAccessIndicator from './MemoryAccessIndicator'
import ToolCallCard from './ToolCallCard'
import { agentConfig } from './agents'

interface AgentOrchestrationViewProps {
  agentStates: Map<string, AgentState>
  activeAgent: string | null
  isStreaming: boolean
}

function ActiveDot() {
  return (
    <motion.div
      animate={{ scale: [1, 1.3, 1], opacity: [1, 0.7, 1] }}
      transition={{ duration: 1.5, repeat: Infinity }}
      style={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        backgroundColor: 'var(--chakra-colors-brand-solid)',
        display: 'inline-block',
      }}
    />
  )
}

function AgentCard({
  name,
  state,
  isActive,
}: {
  name: string
  state: AgentState
  isActive: boolean
}) {
  const [expanded, setExpanded] = useState(true)
  const config = agentConfig(name)
  const Icon = config.icon
  const hasDetails =
    state.toolCalls.length > 0 || state.memoryAccesses.length > 0 || state.thinking.length > 0

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.3 }}
    >
      <Card.Root
        size="sm"
        colorPalette={config.colorPalette}
        borderLeftWidth="3px"
        borderLeftColor={
          isActive
            ? 'colorPalette.solid'
            : state.status === 'complete'
              ? 'green.solid'
              : 'border.emphasized'
        }
        bg={isActive ? 'colorPalette.subtle' : 'bg.panel'}
      >
        <Card.Body py={2} px={3}>
          <Flex
            justify="space-between"
            align="center"
            cursor={hasDetails ? 'pointer' : 'default'}
            onClick={() => hasDetails && setExpanded(!expanded)}
          >
            <HStack gap={2}>
              {isActive && <ActiveDot />}
              <Icon size={16} />
              <Text fontWeight="semibold" fontSize="sm">
                {config.label}
              </Text>
              <Text fontSize="xs" color="fg.subtle" hideBelow="md">
                {config.description}
              </Text>
            </HStack>
            <HStack gap={1}>
              {state.toolCalls.length > 0 && (
                <Badge size="sm" colorPalette={config.colorPalette}>
                  {state.toolCalls.length} tools
                </Badge>
              )}
              {state.memoryAccesses.length > 0 && (
                <Badge size="sm" colorPalette="cyan">
                  <LuDatabase size={10} /> {state.memoryAccesses.length}
                </Badge>
              )}
              <Badge
                size="sm"
                colorPalette={
                  state.status === 'active' ? 'brand' : state.status === 'complete' ? 'green' : 'gray'
                }
              >
                {state.status}
              </Badge>
              {hasDetails && (expanded ? <LuChevronDown size={14} /> : <LuChevronRight size={14} />)}
            </HStack>
          </Flex>

          <AnimatePresence>
            {expanded && hasDetails && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                style={{ overflow: 'hidden' }}
              >
                <VStack gap={1.5} align="stretch" mt={2}>
                  {state.thinking && (
                    // Tail of the accumulated reasoning text, newest last.
                    <Text fontSize="xs" color="fg.muted" fontStyle="italic" lineClamp={3}>
                      {state.thinking.slice(-400)}
                    </Text>
                  )}

                  {state.memoryAccesses.map((access, i) => (
                    <MemoryAccessIndicator
                      key={i}
                      operation={access.operation}
                      tool={access.tool}
                      query={access.query}
                    />
                  ))}

                  {state.toolCalls.map((call, i) => (
                    <ToolCallCard
                      key={i}
                      tool={call.tool}
                      args={call.args}
                      result={call.result}
                      durationMs={call.durationMs}
                      colorPalette={config.colorPalette}
                    />
                  ))}
                </VStack>
              </motion.div>
            )}
          </AnimatePresence>
        </Card.Body>
      </Card.Root>
    </motion.div>
  )
}

/** Live view of the agents working on the current turn. */
export default function AgentOrchestrationView({
  agentStates,
  activeAgent,
  isStreaming,
}: AgentOrchestrationViewProps) {
  if (!isStreaming && agentStates.size === 0) return null

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
      <Box p={3} bg="bg.muted" borderRadius="md" mb={3}>
        <HStack mb={2}>
          <LuBrain size={16} />
          <Heading size="xs" fontFamily="heading">
            Agent Activity
          </Heading>
          {isStreaming && (
            <Badge colorPalette="brand" size="sm">
              <motion.span
                animate={{ opacity: [1, 0.5, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              >
                Live
              </motion.span>
            </Badge>
          )}
        </HStack>
        <VStack gap={2} align="stretch">
          <AnimatePresence>
            {Array.from(agentStates.entries()).map(([name, state]) => (
              <AgentCard key={name} name={name} state={state} isActive={name === activeAgent} />
            ))}
          </AnimatePresence>
        </VStack>
      </Box>
    </motion.div>
  )
}
