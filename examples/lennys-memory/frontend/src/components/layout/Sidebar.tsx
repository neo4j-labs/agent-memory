"use client";

import {
  Box,
  Stack,
  Text,
  Button,
  Flex,
  Heading,
  Link,
  Separator,
  Skeleton,
} from "@chakra-ui/react";
import {
  LuPlus,
  LuMessageSquare,
  LuGithub,
  LuExternalLink,
  LuDatabase,
  LuSparkles,
  LuBookOpen,
} from "react-icons/lu";
import type { QuickStartSuggestion } from "@/lib/types";

interface SidebarProps {
  suggestions: QuickStartSuggestion[];
  isLoading: boolean;
  onNewConversation: () => void;
  onSelectSuggestion: (suggestion: QuickStartSuggestion) => void;
  hasActiveConversation: boolean;
  onSidebarAction?: () => void; // Called after any action (for mobile drawer close)
}

export function Sidebar({
  suggestions,
  isLoading,
  onNewConversation,
  onSelectSuggestion,
  hasActiveConversation,
  onSidebarAction,
}: SidebarProps) {
  const handleNewConversation = () => {
    onNewConversation();
    onSidebarAction?.();
  };

  const handleSelectSuggestion = (suggestion: QuickStartSuggestion) => {
    onSelectSuggestion(suggestion);
    onSidebarAction?.();
  };

  return (
    <Stack h="full" p={{ base: 3, md: 4 }} gap={{ base: 3, md: 4 }}>
      {/* Header */}
      <Flex alignItems="center" gap="2">
        <LuMessageSquare size={20} />
        <Heading size="sm" fontWeight="semibold">
          Conversations
        </Heading>
      </Flex>

      {/* New conversation button */}
      <Button
        w="full"
        size="sm"
        variant={hasActiveConversation ? "outline" : "solid"}
        colorPalette="blue"
        onClick={handleNewConversation}
        minH={{ base: "44px", md: "auto" }}
      >
        <LuPlus />
        New Conversation
      </Button>

      {/* Quick Start section */}
      <Stack gap="2">
        <Flex alignItems="center" gap="2">
          <LuSparkles size={14} />
          <Text fontSize="sm" fontWeight="medium" color="fg.muted">
            Quick Start
          </Text>
        </Flex>
        <Text fontSize="xs" color="fg.muted">
          Click a topic to start a new conversation
        </Text>
      </Stack>

      {/* Suggestions list */}
      <Stack flex="1" gap="1" overflowY="auto">
        {isLoading ? (
          // Loading skeletons
          <>
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} height="60px" borderRadius="md" />
            ))}
          </>
        ) : suggestions.length === 0 ? (
          <Text fontSize="sm" color="fg.muted" textAlign="center" py="8">
            No suggestions available
          </Text>
        ) : (
          suggestions.map((suggestion) => (
            <Box
              key={suggestion.id}
              px="3"
              py={{ base: 3, md: 2 }}
              minH={{ base: "44px", md: "auto" }}
              bg="bg.muted"
              borderRadius="md"
              cursor="pointer"
              _hover={{ bg: "bg.emphasized" }}
              _active={{ bg: "bg.subtle" }}
              onClick={() => handleSelectSuggestion(suggestion)}
            >
              <Text fontSize="sm" color="fg.default" lineClamp={2}>
                {suggestion.firstMessage}
              </Text>
              {(suggestion.category || suggestion.timestamp) && (
                <Text fontSize="xs" color="fg.muted" mt="1">
                  {suggestion.category ??
                    new Date(suggestion.timestamp!).toLocaleDateString()}
                </Text>
              )}
            </Box>
          ))
        )}
      </Stack>

      {/* Branding footer */}
      <Stack gap={{ base: 2, md: 3 }} pt="2">
        <Separator />

        {/* Powered by section */}
        <Stack gap={{ base: 1.5, md: 2 }}>
          <Text fontSize="xs" color="fg.muted" fontWeight="medium">
            Powered by
          </Text>

          <Link
            href="https://github.com/neo4j-labs/agent-memory"
            target="_blank"
            rel="noopener noreferrer"
            _hover={{ textDecoration: "none" }}
          >
            <Flex
              px="3"
              py={{ base: 2.5, md: 2 }}
              minH={{ base: "44px", md: "auto" }}
              bg="blue.subtle"
              borderRadius="md"
              alignItems="center"
              gap="2"
              _hover={{ bg: "blue.100" }}
              _active={{ bg: "blue.200" }}
              transition="background 0.2s"
            >
              <LuDatabase size={16} color="var(--chakra-colors-blue-600)" />
              <Text fontSize="sm" fontWeight="medium" color="blue.700" flex="1">
                @neo4j-labs/agent-memory
              </Text>
              <LuExternalLink size={12} color="var(--chakra-colors-blue-500)" />
            </Flex>
          </Link>

          <Flex gap="2">
            <Link
              href="https://neo4j.com"
              target="_blank"
              rel="noopener noreferrer"
              flex="1"
              _hover={{ textDecoration: "none" }}
            >
              <Flex
                px="2"
                py={{ base: 2, md: 1.5 }}
                minH={{ base: "40px", md: "auto" }}
                bg="bg.muted"
                borderRadius="md"
                alignItems="center"
                justifyContent="center"
                gap="1"
                _hover={{ bg: "bg.emphasized" }}
                _active={{ bg: "bg.subtle" }}
                transition="background 0.2s"
              >
                <Text fontSize="xs" color="fg.muted">
                  Neo4j
                </Text>
              </Flex>
            </Link>

            <Link
              href="https://www.lennysnewsletter.com/podcast"
              target="_blank"
              rel="noopener noreferrer"
              flex="1"
              _hover={{ textDecoration: "none" }}
            >
              <Flex
                px="2"
                py={{ base: 2, md: 1.5 }}
                minH={{ base: "40px", md: "auto" }}
                bg="bg.muted"
                borderRadius="md"
                alignItems="center"
                justifyContent="center"
                gap="1"
                _hover={{ bg: "bg.emphasized" }}
                _active={{ bg: "bg.subtle" }}
                transition="background 0.2s"
              >
                <Text fontSize="xs" color="fg.muted">
                  Lenny&apos;s Podcast
                </Text>
              </Flex>
            </Link>
          </Flex>
        </Stack>

        {/* Blog post and GitHub links */}
        <Flex gap="2">
          <Link
            href="https://neo4j.com/blog/developer/meet-lennys-memory-building-context-graphs-for-ai-agents/"
            target="_blank"
            rel="noopener noreferrer"
            flex="1"
            _hover={{ textDecoration: "none" }}
          >
            <Flex
              px="2"
              py={{ base: 2.5, md: 2 }}
              minH={{ base: "44px", md: "auto" }}
              bg="bg.muted"
              borderRadius="md"
              alignItems="center"
              justifyContent="center"
              gap="1.5"
              _hover={{ bg: "bg.emphasized" }}
              _active={{ bg: "bg.subtle" }}
              transition="background 0.2s"
            >
              <LuBookOpen size={14} />
              <Text fontSize="xs" color="fg.muted">
                Blog
              </Text>
            </Flex>
          </Link>

          <Link
            href="https://github.com/neo4j-labs/agent-memory/tree/main/examples/lennys-memory"
            target="_blank"
            rel="noopener noreferrer"
            flex="1"
            _hover={{ textDecoration: "none" }}
          >
            <Flex
              px="2"
              py={{ base: 2.5, md: 2 }}
              minH={{ base: "44px", md: "auto" }}
              bg="bg.muted"
              borderRadius="md"
              alignItems="center"
              justifyContent="center"
              gap="1.5"
              _hover={{ bg: "bg.emphasized" }}
              _active={{ bg: "bg.subtle" }}
              transition="background 0.2s"
            >
              <LuGithub size={14} />
              <Text fontSize="xs" color="fg.muted">
                Source
              </Text>
            </Flex>
          </Link>
        </Flex>
      </Stack>
    </Stack>
  );
}
