import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Heading,
  Text,
  Card,
  VStack,
  HStack,
  Input,
  Button,
  Badge,
  SimpleGrid,
} from "@chakra-ui/react";
import { AnimatePresence, motion } from "motion/react";
import {
  LuSend,
  LuUser,
  LuCpu,
  LuShield,
  LuSearch,
  LuUsers,
  LuTriangleAlert,
  LuFileText,
  LuActivity,
  LuDatabase,
  LuPlus,
} from "react-icons/lu";
import { useQuery } from "@tanstack/react-query";
import { useAgentStream, type AgentState } from "../../hooks/useAgentStream";
import { getChatHistory, getSessionTraces } from "../../lib/api";
import { useChatSession } from "../../lib/session";
import { AgentOrchestrationView } from "./AgentOrchestrationView";
import { AgentActivityTimeline } from "./AgentActivityTimeline";
import { MarkdownMessage } from "./MarkdownMessage";

interface Message {
  role: "user" | "assistant";
  content: string;
  agents_consulted?: string[];
  tool_calls?: Array<{ tool_name: string; agent?: string }>;
  response_time_ms?: number;
  // Captured stream state for post-completion timeline
  agentStates?: Map<string, AgentState>;
  traceId?: string | null;
  /** True when the turn was replayed from Neo4j rather than streamed here. */
  restored?: boolean;
}

function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      <Box alignSelf={isUser ? "flex-end" : "flex-start"} maxW="85%" mb={4}>
        <HStack gap={2} mb={1} justify={isUser ? "flex-end" : "flex-start"}>
          <Box
            p={1}
            borderRadius="full"
            bg={isUser ? "brand.subtle" : "teal.subtle"}
            color={isUser ? "brand.fg" : "teal.fg"}
          >
            {isUser ? <LuUser size={14} /> : <LuCpu size={14} />}
          </Box>
          <Text fontSize="xs" color="fg.muted">
            {isUser ? "You" : "Financial Advisor"}
          </Text>
          {message.restored && (
            <Badge size="sm" variant="subtle" colorPalette="teal">
              <LuDatabase size={10} /> replayed from Neo4j
            </Badge>
          )}
        </HStack>

        <Box
          bg={isUser ? "brand.solid" : "bg.panel"}
          color={isUser ? "brand.contrast" : "fg"}
          px={4}
          py={3}
          borderRadius="lg"
          shadow={isUser ? "none" : "xs"}
          border={isUser ? "none" : "1px solid"}
          borderColor="border.subtle"
        >
          {isUser ? (
            <Text whiteSpace="pre-wrap">{message.content}</Text>
          ) : (
            <MarkdownMessage content={message.content} />
          )}
        </Box>

        {/* Agent activity timeline for assistant messages */}
        {!isUser && (
          <AgentActivityTimeline
            agentStates={message.agentStates || new Map()}
            agentsConsulted={message.agents_consulted || []}
            totalDurationMs={message.response_time_ms}
            traceId={message.traceId}
          />
        )}
      </Box>
    </motion.div>
  );
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // The session id lives at app level so the Context Graph view can scope
  // itself to this conversation, and so a reload can replay it from Neo4j.
  const { sessionId, setSessionId } = useChatSession();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const {
    isStreaming,
    activeAgent,
    agentStates,
    error,
    delegationChain,
    startStream,
  } = useAgentStream();

  // --- Replay a stored conversation (short-term memory) and its reasoning
  // traces (reasoning memory) when we reopen a session we already have an id
  // for. Without this the audit trail would only ever live in this tab.
  const replaying = !!sessionId && messages.length === 0;
  const historyQuery = useQuery({
    queryKey: ["chat-history", sessionId],
    queryFn: () => getChatHistory(sessionId!),
    enabled: replaying,
    staleTime: Infinity,
    retry: false,
  });
  const tracesQuery = useQuery({
    queryKey: ["session-traces", sessionId],
    queryFn: () => getSessionTraces(sessionId!),
    enabled: replaying,
    staleTime: Infinity,
    retry: false,
  });

  const restoredMessages = useMemo<Message[]>(() => {
    if (!historyQuery.data) return [];
    // `getSessionTraces` returns newest-first; assistant turns run oldest-first.
    const traceIds = [...(tracesQuery.data ?? [])].reverse().map((t) => t.id);
    let assistantTurn = 0;
    return historyQuery.data.messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) =>
        m.role === "assistant"
          ? {
              role: "assistant" as const,
              content: m.content,
              restored: true,
              traceId: traceIds[assistantTurn++] ?? null,
            }
          : { role: "user" as const, content: m.content },
      );
  }, [historyQuery.data, tracesQuery.data]);

  // What the transcript shows: live turns once there are any, otherwise the
  // turns replayed from Neo4j.
  const visibleMessages = messages.length > 0 ? messages : restoredMessages;

  const startNewConversation = useCallback(() => {
    setMessages([]);
    setInput("");
    setSessionId(null);
  }, [setSessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleMessages, agentStates, isStreaming]);

  const handleSend = async () => {
    const prompt = input.trim();
    if (!prompt || isStreaming) return;

    // Seed the transcript with anything replayed from Neo4j plus this turn.
    setMessages((prev) => [
      ...(prev.length > 0 ? prev : restoredMessages),
      { role: "user", content: prompt },
    ]);
    setInput("");

    const outcome = await startStream(prompt, sessionId ?? undefined);
    if (!outcome) return;

    setSessionId(outcome.sessionId ?? null);
    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        content: outcome.response,
        agents_consulted: outcome.agentsConsulted,
        response_time_ms: outcome.totalDurationMs,
        agentStates: outcome.agentStates,
        traceId: outcome.traceId,
      },
    ]);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const suggestedPrompts = [
    {
      title: "Full Compliance Investigation",
      agents: ["KYC", "AML", "Relationship", "Compliance"],
      icon: LuShield,
      color: "red",
      prompt:
        "Run a full compliance investigation on CUST-003 Global Holdings Ltd \u2014 check KYC documents, scan for structuring patterns, trace the shell company network, and screen against sanctions lists",
    },
    {
      title: "Detect Structuring Pattern",
      agents: ["AML"],
      icon: LuTriangleAlert,
      color: "orange",
      prompt:
        "I see four cash deposits of $9,500 each from CUST-003 in late January. Analyze whether this is a structuring pattern and identify where the funds went",
    },
    {
      title: "Compare Customer Risk Profiles",
      agents: ["KYC", "Compliance"],
      icon: LuActivity,
      color: "blue",
      prompt:
        "Compare the risk profiles of all three customers and flag which ones need enhanced due diligence",
    },
    {
      title: "Trace Beneficial Ownership",
      agents: ["Relationship"],
      icon: LuUsers,
      color: "purple",
      prompt:
        "Trace the beneficial ownership chain from Global Holdings Ltd through Shell Corp Cayman and Anonymous Trust Seychelles \u2014 who ultimately controls these entities?",
    },
    {
      title: "Investigate Wire Transfers",
      agents: ["AML", "KYC"],
      icon: LuSearch,
      color: "teal",
      prompt:
        "Maria Garcia (CUST-002) has rapid wire transfers totaling over $280K. Investigate whether her import/export business justifies this transaction volume",
    },
    {
      title: "Generate SAR Report",
      agents: ["Compliance", "AML"],
      icon: LuFileText,
      color: "yellow",
      prompt:
        "Generate a Suspicious Activity Report for the $250,000 wire from an unknown offshore entity to CUST-003 that was moved to Shell Corp Cayman the next day",
    },
  ];

  return (
    <Box h="calc(100vh - 100px)" display="flex" flexDirection="column">
      <HStack justify="space-between" mb={4}>
        <Heading size="lg">AI Financial Advisor</Heading>
        <HStack gap={2}>
          {isStreaming && (
            <Badge colorPalette="green" variant="solid" size="sm">
              Streaming
            </Badge>
          )}
          {sessionId && (
            <Badge variant="outline" fontFamily="mono">
              Session: {sessionId.slice(0, 8)}…
            </Badge>
          )}
          {sessionId && (
            <Button
              size="xs"
              variant="outline"
              onClick={startNewConversation}
              disabled={isStreaming}
            >
              <LuPlus /> New conversation
            </Button>
          )}
        </HStack>
      </HStack>

      {/* Messages area */}
      <Card.Root flex="1" mb={4} overflow="hidden">
        <Card.Body overflowY="auto" display="flex" flexDirection="column" p={4}>
          {visibleMessages.length === 0 && !isStreaming ? (
            <VStack justify="center" flex="1" gap={6} py={4}>
              <VStack gap={1}>
                <Heading size="md" color="fg.muted">
                  AI Financial Advisor
                </Heading>
                <Text color="fg.muted" textAlign="center" fontSize="sm">
                  Multi-agent compliance investigations powered by Google ADK
                  and Neo4j
                </Text>
              </VStack>
              <SimpleGrid
                columns={{ base: 1, md: 2, lg: 3 }}
                gap={3}
                w="full"
                px={2}
              >
                {suggestedPrompts.map((item, i) => {
                  const Icon = item.icon;
                  return (
                    <Card.Root
                      key={i}
                      variant="outline"
                      size="sm"
                      cursor="pointer"
                      _hover={{
                        shadow: "md",
                        borderColor: `${item.color}.300`,
                        bg: `${item.color}.50`,
                      }}
                      transition="all 0.2s"
                      onClick={() => setInput(item.prompt)}
                    >
                      <Card.Body gap={2}>
                        <HStack gap={2}>
                          <Box color={`${item.color}.500`}>
                            <Icon size={16} />
                          </Box>
                          <Text fontWeight="semibold" fontSize="sm">
                            {item.title}
                          </Text>
                        </HStack>
                        <Text fontSize="xs" color="fg.muted" lineClamp={2}>
                          {item.prompt}
                        </Text>
                        <HStack gap={1} flexWrap="wrap" mt={1}>
                          {item.agents.map((agent) => (
                            <Badge
                              key={agent}
                              size="sm"
                              variant="subtle"
                              colorPalette={item.color}
                            >
                              {agent}
                            </Badge>
                          ))}
                        </HStack>
                      </Card.Body>
                    </Card.Root>
                  );
                })}
              </SimpleGrid>
            </VStack>
          ) : (
            <VStack align="stretch" gap={0}>
              <AnimatePresence>
                {visibleMessages.map((msg, i) => (
                  <ChatMessage key={i} message={msg} />
                ))}
              </AnimatePresence>

              {/* Live agent orchestration view during streaming */}
              <AnimatePresence>
                {isStreaming && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                  >
                    <Box mb={4}>
                      <AgentOrchestrationView
                        agentStates={agentStates}
                        activeAgent={activeAgent}
                        delegationChain={delegationChain}
                        isStreaming={isStreaming}
                      />
                    </Box>
                  </motion.div>
                )}
              </AnimatePresence>

              <div ref={messagesEndRef} />
            </VStack>
          )}
        </Card.Body>
      </Card.Root>

      {/* Input area */}
      <HStack>
        <Input
          placeholder="Ask about customer investigations, risk assessments..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyPress}
          disabled={isStreaming}
          size="lg"
        />
        <Button
          colorPalette="blue"
          onClick={() => void handleSend()}
          disabled={!input.trim() || isStreaming}
          size="lg"
          aria-label="Send message"
        >
          <LuSend />
        </Button>
      </HStack>

      {error && (
        <Text color="red.500" fontSize="sm" mt={2}>
          Error: {error}
        </Text>
      )}
    </Box>
  );
}
