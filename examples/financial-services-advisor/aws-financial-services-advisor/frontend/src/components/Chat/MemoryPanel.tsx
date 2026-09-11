import { Badge, Box, Card, Flex, HStack, Heading, IconButton, Spinner, Text, VStack } from '@chakra-ui/react'
import { useQuery } from '@tanstack/react-query'
import {
  LuBrain,
  LuDatabase,
  LuMessageSquare,
  LuRefreshCw,
  LuWrench,
} from 'react-icons/lu'
import { chatApi, memoryApi, tracesApi } from '../../lib/api'

interface MemoryPanelProps {
  sessionId: string | null
  /** Bumped after each completed turn so the panel refetches. */
  memoryVersion: number
}

function SectionHeading({
  icon: Icon,
  title,
  caption,
  count,
}: {
  icon: React.ElementType
  title: string
  caption: string
  count?: number
}) {
  return (
    <Box>
      <HStack gap={2}>
        <Icon size={14} />
        <Text fontWeight="semibold" fontSize="sm" fontFamily="heading">
          {title}
        </Text>
        {count !== undefined && (
          <Badge size="sm" colorPalette="brand">
            {count}
          </Badge>
        )}
      </HStack>
      <Text fontSize="xs" color="fg.subtle">
        {caption}
      </Text>
    </Box>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <Text fontSize="xs" color="fg.muted">
      {children}
    </Text>
  )
}

/**
 * What the library stored for this conversation, read back from Neo4j.
 *
 * - Short-term: `GET /api/chat/history/{session}` -> `client.short_term`
 * - Long-term: `GET /api/memory/context` (optional, see `memoryApi.getContext`)
 * - Reasoning: `GET /api/traces/{session}` -> `client.reasoning`
 *
 * Everything here is fetched from the backend rather than kept in React state,
 * so it survives a page reload - which is the point of persisting memory.
 */
export default function MemoryPanel({ sessionId, memoryVersion }: MemoryPanelProps) {
  const enabled = Boolean(sessionId)

  const messages = useQuery({
    queryKey: ['memory', 'messages', sessionId, memoryVersion],
    queryFn: () => chatApi.getHistory(sessionId as string),
    enabled,
  })

  const context = useQuery({
    queryKey: ['memory', 'context', sessionId, memoryVersion],
    queryFn: () => memoryApi.getContext(sessionId as string),
    enabled,
  })

  const traces = useQuery({
    queryKey: ['memory', 'traces', sessionId, memoryVersion],
    queryFn: () => tracesApi.getSessionTraces(sessionId as string),
    enabled,
  })

  const refetchAll = () => {
    void messages.refetch()
    void context.refetch()
    void traces.refetch()
  }

  const isLoading = messages.isLoading || context.isLoading || traces.isLoading

  return (
    <Card.Root w={{ base: 'full', xl: '340px' }} flexShrink={0} overflow="hidden">
      <Card.Header pb={2}>
        <Flex justify="space-between" align="center">
          <HStack gap={2}>
            <LuDatabase size={16} />
            <Heading size="sm" fontFamily="heading">
              Agent Memory
            </Heading>
            {isLoading && <Spinner size="xs" />}
          </HStack>
          <IconButton
            aria-label="Refresh memory"
            title="Refresh"
            size="xs"
            variant="ghost"
            onClick={refetchAll}
            disabled={!enabled}
          >
            <LuRefreshCw />
          </IconButton>
        </Flex>
        <Text fontSize="xs" color="fg.subtle">
          {sessionId ? `session ${sessionId.slice(0, 8)}…` : 'Send a message to start a session'}
        </Text>
      </Card.Header>

      <Card.Body overflowY="auto" pt={2}>
        <VStack align="stretch" gap={5}>
          {/* Short-term memory */}
          <Box>
            <SectionHeading
              icon={LuMessageSquare}
              title="Short-term"
              caption="Conversation stored as (:Conversation)-[:HAS_MESSAGE]->(:Message)"
              count={messages.data?.length}
            />
            <VStack align="stretch" gap={1.5} mt={2}>
              {messages.isError && <Empty>Could not load the conversation.</Empty>}
              {messages.data?.length === 0 && <Empty>No messages stored yet.</Empty>}
              {messages.data?.slice(-6).map((message, i) => (
                <Flex
                  key={`${message.role}-${i}`}
                  gap={2}
                  p={2}
                  borderRadius="md"
                  bg="bg.muted"
                  fontSize="xs"
                >
                  <Badge size="sm" colorPalette={message.role === 'user' ? 'teal' : 'brand'}>
                    {message.role}
                  </Badge>
                  <Text lineClamp={3} color="fg.muted">
                    {message.content}
                  </Text>
                </Flex>
              ))}
            </VStack>
          </Box>

          {/* Long-term memory */}
          <Box>
            <SectionHeading
              icon={LuDatabase}
              title="Long-term"
              caption="Entities and preferences extracted from the conversation"
              count={context.data?.entities.length}
            />
            <Box mt={2}>
              {context.isError && <Empty>Could not load long-term memory.</Empty>}
              {!context.isError && context.data === null && (
                <Empty>
                  Entities are extracted on every turn, but the backend does not expose them
                  yet — add <code>GET /api/memory/context</code> to light this up (see the
                  frontend README, “Known gaps”).
                </Empty>
              )}
              {context.data && (
                <VStack align="stretch" gap={2}>
                  <Flex gap={1.5} flexWrap="wrap">
                    {context.data.entities.length === 0 && <Empty>No entities yet.</Empty>}
                    {context.data.entities.slice(0, 20).map((entity) => (
                      <Badge key={entity.name} size="sm" variant="outline" title={entity.type ?? ''}>
                        {entity.name}
                      </Badge>
                    ))}
                  </Flex>
                  {context.data.preferences.length > 0 && (
                    <VStack align="stretch" gap={1}>
                      {context.data.preferences.map((preference) => (
                        <Text key={`${preference.category}-${preference.value}`} fontSize="xs">
                          <Text as="span" color="fg.subtle">
                            {preference.category}:{' '}
                          </Text>
                          {preference.value}
                        </Text>
                      ))}
                    </VStack>
                  )}
                </VStack>
              )}
            </Box>
          </Box>

          {/* Reasoning memory */}
          <Box>
            <SectionHeading
              icon={LuBrain}
              title="Reasoning"
              caption="Traces, steps and tool calls recorded by client.reasoning"
              count={traces.data?.length}
            />
            <VStack align="stretch" gap={2} mt={2}>
              {traces.isError && <Empty>Could not load reasoning traces.</Empty>}
              {traces.data?.length === 0 && <Empty>No traces recorded for this session.</Empty>}
              {traces.data?.slice(0, 4).map((trace) => (
                <Box key={trace.id} p={2} borderRadius="md" borderWidth="1px" borderColor="border">
                  <HStack gap={2} mb={1}>
                    <Badge size="sm" colorPalette={trace.success === false ? 'red' : 'green'}>
                      {trace.success === false ? 'failed' : 'complete'}
                    </Badge>
                    <Text fontSize="xs" fontFamily="mono" color="fg.subtle">
                      {trace.id.slice(0, 8)}…
                    </Text>
                  </HStack>
                  <Text fontSize="xs" lineClamp={2}>
                    {trace.task}
                  </Text>
                  <VStack align="stretch" gap={1} mt={1.5}>
                    {trace.steps.map((step) => (
                      <Box key={step.id} fontSize="xs" color="fg.muted">
                        <Text lineClamp={2}>
                          {step.step_number}. {step.action || step.thought || 'step'}
                        </Text>
                        {step.tool_calls.length > 0 && (
                          <Flex gap={1} flexWrap="wrap" mt={1}>
                            {step.tool_calls.map((call, i) => (
                              <Badge
                                key={`${call.tool_name}-${i}`}
                                size="sm"
                                variant="outline"
                                fontFamily="mono"
                              >
                                <LuWrench size={10} /> {call.tool_name}
                              </Badge>
                            ))}
                          </Flex>
                        )}
                      </Box>
                    ))}
                  </VStack>
                </Box>
              ))}
            </VStack>
          </Box>
        </VStack>
      </Card.Body>
    </Card.Root>
  )
}
