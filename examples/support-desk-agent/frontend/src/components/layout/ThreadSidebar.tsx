"use client";

import { Badge, Box, Button, Flex, Stack, Text } from "@chakra-ui/react";
import { LuMessageSquare, LuPlus, LuSprout } from "react-icons/lu";
import { formatDateTime } from "@/lib/format";
import type { ThreadSummary } from "@/lib/types";

interface ThreadSidebarProps {
  threads: ThreadSummary[];
  loaded: boolean;
  activeThreadId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
}

function ThreadRow({
  thread,
  active,
  onSelect,
}: {
  thread: ThreadSummary;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <Box
      as="button"
      w="full"
      textAlign="left"
      px="3"
      py="2"
      borderRadius="md"
      bg={active ? "brand.subtle" : "transparent"}
      borderLeftWidth="3px"
      borderLeftColor={active ? "brand.solid" : "transparent"}
      _hover={{ bg: active ? "brand.subtle" : "bg.muted" }}
      onClick={() => onSelect(thread.id)}
      aria-current={active ? "true" : undefined}
    >
      <Flex alignItems="center" gap="2">
        <Text
          flex="1"
          fontSize="sm"
          fontWeight={active ? "medium" : "normal"}
          truncate
        >
          {thread.title || thread.id}
        </Text>
        {thread.seeded ? (
          <Badge size="xs" variant="outline" colorPalette="gray">
            seeded
          </Badge>
        ) : null}
      </Flex>
      <Text fontSize="xs" color="fg.muted">
        {thread.message_count} message{thread.message_count === 1 ? "" : "s"}
        {thread.updated_at ? ` · ${formatDateTime(thread.updated_at)}` : ""}
      </Text>
    </Box>
  );
}

/**
 * Left-hand thread list. Seeded conversations (`seed-*` sessions written by
 * `make seed`) are listed under their own heading so they read as fixtures,
 * not as the user's own chats.
 */
export function ThreadSidebar({
  threads,
  loaded,
  activeThreadId,
  onSelect,
  onNewChat,
}: ThreadSidebarProps) {
  const own = threads.filter((t) => !t.seeded);
  const seeded = threads.filter((t) => t.seeded);

  return (
    <Stack h="full" p="3" gap="3" as="nav" aria-label="Conversations">
      <Button w="full" size="sm" colorPalette="brand" onClick={onNewChat}>
        <LuPlus />
        New chat
      </Button>

      <Stack flex="1" gap="4" overflowY="auto" minH="0">
        <Stack gap="1">
          <Flex alignItems="center" gap="2" px="1" color="fg.muted">
            <LuMessageSquare size={14} />
            <Text fontSize="xs" fontWeight="semibold" textTransform="uppercase">
              Your chats
            </Text>
          </Flex>
          {own.length === 0 ? (
            <Text fontSize="xs" color="fg.muted" px="1">
              {loaded ? "None yet — start a new chat." : "Loading…"}
            </Text>
          ) : (
            own.map((thread) => (
              <ThreadRow
                key={thread.id}
                thread={thread}
                active={thread.id === activeThreadId}
                onSelect={onSelect}
              />
            ))
          )}
        </Stack>

        <Stack gap="1">
          <Flex alignItems="center" gap="2" px="1" color="fg.muted">
            <LuSprout size={14} />
            <Text fontSize="xs" fontWeight="semibold" textTransform="uppercase">
              Seeded conversations
            </Text>
          </Flex>
          {seeded.length === 0 ? (
            <Text fontSize="xs" color="fg.muted" px="1">
              {loaded ? (
                <>
                  None — run <code>make seed</code>.
                </>
              ) : (
                "Loading…"
              )}
            </Text>
          ) : (
            seeded.map((thread) => (
              <ThreadRow
                key={thread.id}
                thread={thread}
                active={thread.id === activeThreadId}
                onSelect={onSelect}
              />
            ))
          )}
        </Stack>
      </Stack>
    </Stack>
  );
}
