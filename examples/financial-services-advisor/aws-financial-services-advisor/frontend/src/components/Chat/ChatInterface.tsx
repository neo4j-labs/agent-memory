import { Box, Button, Card, Flex, Heading, Input, Spinner, Text, VStack } from '@chakra-ui/react'
import { useEffect, useRef, useState } from 'react'
import { FiCpu, FiSend, FiSquare, FiUser } from 'react-icons/fi'
import { useAgentStream, type AgentState, type StreamResult } from '../../hooks/useAgentStream'
import AgentActivityTimeline from './AgentActivityTimeline'
import AgentOrchestrationView from './AgentOrchestrationView'
import MemoryPanel from './MemoryPanel'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  isLoading?: boolean
  agentStates?: Map<string, AgentState>
  streamResult?: StreamResult | null
}

const SUGGESTIONS = [
  'Investigate customer CUST-003 for potential money laundering',
  'Run a compliance check for customer CUST-001',
  'Analyze the network connections of Global Holdings Ltd',
]

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  // Generated up front (the backend honours `session_id`), so the memory panel
  // can read this conversation back while the very first turn is still running.
  const [sessionId, setSessionId] = useState<string>(() => crypto.randomUUID())
  // Bumped when a turn finishes, so the memory panel refetches from Neo4j.
  const [memoryVersion, setMemoryVersion] = useState(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const { isStreaming, activeAgent, agentStates, startStream, stop } = useAgentStream()

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming])

  const handleSend = async () => {
    const content = input.trim()
    if (!content || isStreaming) return

    const id = Date.now().toString()
    const loadingId = `${id}-loading`
    setMessages((prev) => [
      ...prev,
      { id, role: 'user', content },
      { id: loadingId, role: 'assistant', content: '', isLoading: true },
    ])
    setInput('')

    // `startStream` resolves when the turn ends, so the reply, the agent
    // activity and the memory refresh are all applied here - no effects
    // mirroring hook state into this component.
    const outcome = await startStream(content, sessionId)

    const text = outcome.response
      ? outcome.response
      : outcome.aborted
        ? '_(stopped)_'
        : `Error: ${outcome.error ?? 'the agent returned no response'}`

    setMessages((prev) =>
      prev.map((message) =>
        message.id === loadingId
          ? {
              ...message,
              content: text,
              isLoading: false,
              agentStates: outcome.agentStates,
              streamResult: outcome.streamResult,
            }
          : message,
      ),
    )

    if (outcome.streamResult?.sessionId) setSessionId(outcome.streamResult.sessionId)
    // Memory was written during the turn; tell the panel to read it back.
    setMemoryVersion((version) => version + 1)
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  return (
    <Box h="calc(100dvh - 48px)">
      <Flex direction="column" h="full" gap={4}>
        {/* Header */}
        <Box>
          <Heading size="lg" fontFamily="heading">
            AI Compliance Advisor
          </Heading>
          <Text color="fg.muted">
            Ask questions about customers, investigations, or compliance requirements
          </Text>
        </Box>

        <Flex flex="1" gap={4} minH={0} direction={{ base: 'column', xl: 'row' }}>
          {/* Chat */}
          <Card.Root flex="1" overflow="hidden" minW={0}>
            <Card.Body p={0} display="flex" flexDirection="column" h="full">
              <Box flex="1" overflowY="auto" p={4}>
                {messages.length === 0 ? (
                  <Flex direction="column" align="center" justify="center" h="full" gap={2}>
                    <Box color="fg.subtle">
                      <FiCpu size={48} />
                    </Box>
                    <Text mt={2} fontSize="lg">
                      Start a conversation
                    </Text>
                    <Text fontSize="sm" color="fg.muted" textAlign="center" maxW="420px">
                      Ask about customer risk assessments, investigation findings, or compliance
                      requirements. Every agent tool queries real data from Neo4j, and each turn
                      is written to agent memory.
                    </Text>
                    <VStack mt={4} gap={2}>
                      {SUGGESTIONS.map((suggestion) => (
                        <Button
                          key={suggestion}
                          size="sm"
                          variant="outline"
                          onClick={() => setInput(suggestion)}
                        >
                          {suggestion}
                        </Button>
                      ))}
                    </VStack>
                  </Flex>
                ) : (
                  <VStack gap={4} align="stretch">
                    {messages.map((message) => (
                      <Box key={message.id}>
                        <Flex justify={message.role === 'user' ? 'flex-end' : 'flex-start'}>
                          <Flex
                            maxW="85%"
                            colorPalette={message.role === 'user' ? 'brand' : 'gray'}
                            bg={message.role === 'user' ? 'colorPalette.solid' : 'bg.muted'}
                            color={message.role === 'user' ? 'colorPalette.contrast' : 'fg'}
                            borderRadius="lg"
                            p={4}
                            gap={3}
                          >
                            <Box
                              p={2}
                              borderRadius="full"
                              bg={message.role === 'user' ? 'colorPalette.emphasized' : 'bg.subtle'}
                              h="fit-content"
                            >
                              {message.role === 'user' ? <FiUser size={16} /> : <FiCpu size={16} />}
                            </Box>
                            <Box minW={0}>
                              {message.isLoading ? (
                                <Flex align="center" gap={2}>
                                  <Spinner size="sm" />
                                  <Text>Investigating…</Text>
                                </Flex>
                              ) : (
                                <Text whiteSpace="pre-wrap">{message.content}</Text>
                              )}
                            </Box>
                          </Flex>
                        </Flex>

                        {message.role === 'assistant' && !message.isLoading && message.agentStates && (
                          <AgentActivityTimeline
                            agentStates={message.agentStates}
                            streamResult={message.streamResult ?? null}
                          />
                        )}
                      </Box>
                    ))}

                    {isStreaming && (
                      <AgentOrchestrationView
                        agentStates={agentStates}
                        activeAgent={activeAgent}
                        isStreaming={isStreaming}
                      />
                    )}

                    <div ref={messagesEndRef} />
                  </VStack>
                )}
              </Box>

              {/* Input */}
              <Box p={4} borderTopWidth="1px" borderColor="border">
                <Flex gap={2}>
                  <Input
                    placeholder="Ask about compliance, customers, or investigations…"
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isStreaming}
                  />
                  {isStreaming ? (
                    <Button colorPalette="red" variant="outline" onClick={stop}>
                      <FiSquare /> Stop
                    </Button>
                  ) : (
                    <Button
                      colorPalette="brand"
                      onClick={() => void handleSend()}
                      disabled={!input.trim()}
                    >
                      <FiSend />
                    </Button>
                  )}
                </Flex>
                <Text fontSize="xs" color="fg.subtle" mt={2}>
                  Session: {sessionId.slice(0, 8)}…
                </Text>
              </Box>
            </Card.Body>
          </Card.Root>

          {/* What the agent remembered */}
          <MemoryPanel sessionId={sessionId} memoryVersion={memoryVersion} />
        </Flex>
      </Flex>
    </Box>
  )
}
