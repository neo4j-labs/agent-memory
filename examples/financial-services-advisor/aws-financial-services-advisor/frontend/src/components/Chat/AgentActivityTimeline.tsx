import { Badge, Box, Flex, HStack, Text, VStack } from '@chakra-ui/react'
import { LuBrain, LuClock, LuWrench } from 'react-icons/lu'
import type { AgentState, StreamResult } from '../../hooks/useAgentStream'
import { agentConfig } from './agents'

interface AgentActivityTimelineProps {
  agentStates: Map<string, AgentState>
  streamResult: StreamResult | null
}

/** Post-run summary of one turn: which agents ran and which tools they called. */
export default function AgentActivityTimeline({
  agentStates,
  streamResult,
}: AgentActivityTimelineProps) {
  if (agentStates.size === 0 && !streamResult) return null

  const totalTools = Array.from(agentStates.values()).reduce(
    (sum, state) => sum + state.toolCalls.length,
    0,
  )

  return (
    <Box p={3} bg="bg.muted" borderRadius="md" mt={2} mb={2}>
      <Text fontWeight="semibold" fontSize="sm" mb={2} fontFamily="heading">
        Investigation Summary
      </Text>

      <VStack gap={2} align="stretch">
        {Array.from(agentStates.entries()).map(([name, state]) => {
          const config = agentConfig(name)
          const Icon = config.icon

          return (
            <Flex key={name} align="start" gap={2} colorPalette={config.colorPalette}>
              <Box p={1} borderRadius="full" bg="colorPalette.muted" color="colorPalette.fg" mt={0.5}>
                <Icon size={12} />
              </Box>
              <Box flex={1} minW={0}>
                <HStack gap={1} mb={0.5}>
                  <Text fontSize="xs" fontWeight="medium">
                    {config.label}
                  </Text>
                  <Badge
                    size="sm"
                    colorPalette={state.status === 'complete' ? 'green' : 'gray'}
                  >
                    {state.status}
                  </Badge>
                </HStack>
                {state.toolCalls.length > 0 && (
                  <HStack gap={1} flexWrap="wrap">
                    {state.toolCalls.map((call, i) => (
                      <Badge key={i} size="sm" variant="outline" fontFamily="mono">
                        <LuWrench size={10} /> {call.tool}
                      </Badge>
                    ))}
                  </HStack>
                )}
              </Box>
            </Flex>
          )
        })}
      </VStack>

      {streamResult && (
        <Flex
          mt={3}
          pt={2}
          borderTopWidth="1px"
          borderColor="border"
          gap={4}
          fontSize="xs"
          color="fg.muted"
          flexWrap="wrap"
        >
          <HStack gap={1}>
            <LuBrain size={12} />
            <Text>{streamResult.agentsConsulted.length} agents</Text>
          </HStack>
          <HStack gap={1}>
            <LuWrench size={12} />
            <Text>{totalTools} tools</Text>
          </HStack>
          <HStack gap={1}>
            <LuClock size={12} />
            <Text>{(streamResult.totalDurationMs / 1000).toFixed(1)}s</Text>
          </HStack>
          {streamResult.traceId && (
            <Text fontFamily="mono" color="fg.subtle">
              trace: {streamResult.traceId.slice(0, 8)}…
            </Text>
          )}
        </Flex>
      )}
    </Box>
  )
}
