"use client";

import {
  Box,
  Flex,
  Stack,
  Text,
  SimpleGrid,
  Spinner,
  useBreakpointValue,
  Alert,
  CloseButton,
} from "@chakra-ui/react";
import { useRef, useEffect } from "react";
import { LuBot, LuBrain, LuSparkles } from "react-icons/lu";
import { MessageList } from "./MessageList";
import { PromptInput } from "./PromptInput";
import type { Message } from "@/lib/types";

// Suggested prompts for the empty state - designed to showcase different features
const SUGGESTED_PROMPTS = [
  {
    title: "Explore a Topic",
    prompt:
      "Use memory graph search to explore what guests say about scaling engineering teams",
    // Showcases: MemoryGraphCard (vector search + graph traversal)
  },
  {
    title: "Who is Brian Chesky?",
    prompt: "Tell me about Brian Chesky",
    // Showcases: EntityCard with Wikipedia enrichment, image, description
  },
  {
    title: "Top Companies",
    prompt: "What are the most mentioned companies in the podcast?",
    // Showcases: StatsCard with bar visualization
  },
  {
    title: "Related Entities",
    prompt: "What entities are related to Airbnb?",
    // Showcases: GraphCard with NVL visualization
  },
  {
    title: "Speaker Quotes",
    prompt:
      "What did Brian Chesky say about micromanagement and being in the details?",
    // Showcases: DataCard with podcast transcript results
  },
  {
    title: "Map View",
    prompt: "Show me locations mentioned in the Brian Chesky episode",
    // Showcases: MapCard with geographic visualization
  },
];

interface ChatContainerProps {
  messages: Message[];
  isStreaming: boolean;
  error: string | null;
  onSendMessage: (content: string) => void;
  onClearError?: () => void;
}

export function ChatContainer({
  messages,
  isStreaming,
  error,
  onSendMessage,
  onClearError,
}: ChatContainerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Responsive values - 2x2 grid on mobile for better use of space
  // Default to false/2 during SSR to avoid hydration mismatch
  const isMobile = useBreakpointValue({ base: true, sm: false }) ?? false;
  const promptColumns = useBreakpointValue({ base: 2, sm: 2, lg: 3 }) ?? 2;

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <Flex direction="column" h="full" overflow="hidden" flex="1">
      {/* Error display */}
      {error && (
        <Box px={{ base: 2, md: 4 }} pt={{ base: 2, md: 4 }}>
          <Alert.Root status="error">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>Error</Alert.Title>
              <Alert.Description>{error}</Alert.Description>
            </Alert.Content>
            {onClearError && (
              <CloseButton
                size="sm"
                onClick={onClearError}
                pos="relative"
                top="-2"
                insetEnd="-2"
              />
            )}
          </Alert.Root>
        </Box>
      )}

      {/* Messages area */}
      <Box ref={scrollRef} flex="1" overflowY="auto" p={{ base: 2, md: 4 }}>
        {messages.length === 0 ? (
          <Flex
            h="full"
            alignItems="center"
            justifyContent="center"
            p={{ base: 2, md: 4 }}
          >
            <Stack
              textAlign="center"
              gap={{ base: 4, md: 6 }}
              maxW="3xl"
              w="full"
            >
              <Stack gap="2" align="center">
                <Box
                  color="brand.500"
                  mb={2}
                  p={{ base: 3, md: 4 }}
                  bg="brand.subtle"
                  borderRadius="full"
                >
                  <LuBrain size={isMobile ? 32 : 40} />
                </Box>
                <Text
                  fontSize={{ base: "lg", md: "xl" }}
                  fontWeight="semibold"
                  fontFamily="heading"
                >
                  Ask about Lenny&apos;s Podcast
                </Text>
                <Text
                  color="fg.muted"
                  fontSize={{ base: "xs", md: "sm" }}
                  maxW="md"
                  px={2}
                >
                  Explore insights from 299 podcast episodes with AI-powered
                  graph memory. Click a topic or type your own question.
                </Text>
              </Stack>

              <SimpleGrid columns={promptColumns} gap={{ base: 2, md: 3 }}>
                {SUGGESTED_PROMPTS.slice(0, isMobile ? 4 : 6).map((item) => (
                  <Box
                    key={item.title}
                    as="button"
                    p={{ base: 2.5, md: 4 }}
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="border.subtle"
                    bg="bg.panel"
                    textAlign="left"
                    cursor="pointer"
                    transition="all 0.2s"
                    _hover={{
                      borderColor: "brand.500",
                      bg: "brand.subtle",
                      transform: "translateY(-1px)",
                      boxShadow: "sm",
                    }}
                    _active={{
                      transform: "scale(0.98)",
                    }}
                    _focusVisible={{
                      outline: "2px solid",
                      outlineColor: "brand.500",
                      outlineOffset: "2px",
                    }}
                    onClick={() => !isStreaming && onSendMessage(item.prompt)}
                    opacity={isStreaming ? 0.5 : 1}
                    pointerEvents={isStreaming ? "none" : "auto"}
                    minH={{ base: "auto", md: "auto" }}
                  >
                    <Flex align="center" gap={1.5} mb={{ base: 0.5, md: 2 }}>
                      <Box color="brand.500" flexShrink={0}>
                        <LuSparkles size={isMobile ? 14 : 16} />
                      </Box>
                      <Text
                        fontSize={{ base: "xs", md: "sm" }}
                        fontWeight="medium"
                        color="fg.default"
                        lineClamp={1}
                      >
                        {item.title}
                      </Text>
                    </Flex>
                    <Text
                      fontSize="xs"
                      color="fg.muted"
                      lineHeight="tall"
                      lineClamp={{ base: 2, md: 3 }}
                      hideBelow="sm"
                    >
                      {item.prompt}
                    </Text>
                  </Box>
                ))}
              </SimpleGrid>
            </Stack>
          </Flex>
        ) : (
          <Stack gap={{ base: 3, md: 4 }} maxW="4xl" mx="auto">
            <MessageList messages={messages} />

            {/* Streaming indicator */}
            {isStreaming && (
              <Flex gap="3" alignItems="flex-start" px="4" pb="4">
                <Flex
                  w="8"
                  h="8"
                  borderRadius="full"
                  bg="brand.subtle"
                  alignItems="center"
                  justifyContent="center"
                  flexShrink={0}
                >
                  <LuBot size={16} />
                </Flex>
                <Flex
                  alignItems="center"
                  gap="3"
                  px="4"
                  py="3"
                  bg="brand.subtle"
                  borderRadius="lg"
                  border="1px solid"
                  borderColor="brand.muted"
                >
                  <Spinner size="sm" color="brand.500" borderWidth="2px" />
                  <Text fontSize="sm" color="brand.fg" fontWeight="medium">
                    Thinking and searching podcasts...
                  </Text>
                </Flex>
              </Flex>
            )}
          </Stack>
        )}
      </Box>

      {/* Input area */}
      <Box
        p={{ base: 2, md: 4 }}
        borderTopWidth="1px"
        borderColor="border.subtle"
        bg="bg.panel"
      >
        <PromptInput
          onSend={onSendMessage}
          isLoading={isStreaming}
          placeholder="Ask about Lenny&apos;s Podcast..."
        />
      </Box>
    </Flex>
  );
}
