"use client";

import {
  Button,
  Flex,
  Heading,
  IconButton,
  Stack,
  Switch,
  Text,
} from "@chakra-ui/react";
import { LuBrain, LuMessageSquare, LuPlus, LuTrash2 } from "react-icons/lu";
import type { Thread } from "@/lib/types";

interface SidebarProps {
  threads: Thread[];
  activeThreadId: string | null;
  onSelectThread: (id: string) => void;
  onCreateThread: () => void;
  onDeleteThread: (id: string) => void;
  memoryEnabled: boolean;
  onToggleMemory: (enabled: boolean) => void;
}

export function Sidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onCreateThread,
  onDeleteThread,
  memoryEnabled,
  onToggleMemory,
}: SidebarProps) {
  return (
    <Stack h="full" p="4" gap="4">
      <Flex alignItems="center" gap="2">
        <LuMessageSquare size={20} />
        <Heading size="sm" fontWeight="semibold">
          Conversations
        </Heading>
      </Flex>

      <Button
        w="full"
        size="sm"
        variant="outline"
        onClick={() => onCreateThread()}
      >
        <LuPlus />
        New conversation
      </Button>

      {/* Memory toggle — a real Switch, so it is keyboard-operable and
          announces its state to assistive tech. */}
      <Switch.Root
        checked={memoryEnabled}
        onCheckedChange={(details) => onToggleMemory(details.checked)}
        colorPalette="brand"
        px="3"
        py="2"
        borderRadius="md"
        bg={memoryEnabled ? "brand.subtle" : "bg.muted"}
      >
        <Switch.HiddenInput />
        <Flex alignItems="center" gap="2" flex="1">
          <LuBrain size={16} />
          <Switch.Label flex="1" fontSize="sm">
            Memory
          </Switch.Label>
        </Flex>
        <Switch.Control>
          <Switch.Thumb />
        </Switch.Control>
      </Switch.Root>

      {/* Thread list */}
      <Stack flex="1" gap="1" overflowY="auto">
        {threads.length === 0 ? (
          <Text fontSize="sm" color="fg.muted" textAlign="center" py="8">
            No conversations yet
          </Text>
        ) : (
          threads.map((thread) => (
            <Flex
              key={thread.id}
              className="group"
              px="3"
              py="2"
              bg={
                activeThreadId === thread.id ? "bg.emphasized" : "transparent"
              }
              borderRadius="md"
              cursor="pointer"
              _hover={{ bg: "bg.muted" }}
              onClick={() => onSelectThread(thread.id)}
              alignItems="center"
              gap="2"
            >
              <Text
                flex="1"
                fontSize="sm"
                truncate
                color={activeThreadId === thread.id ? "fg" : "fg.muted"}
              >
                {thread.title}
              </Text>
              <IconButton
                aria-label={`Delete ${thread.title}`}
                variant="ghost"
                size="xs"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteThread(thread.id);
                }}
                opacity="0"
                // `.group` on the row above is what makes this selector match;
                // without it the delete button stayed permanently invisible.
                _groupHover={{ opacity: 1 }}
                _focusVisible={{ opacity: 1 }}
              >
                <LuTrash2 size={14} />
              </IconButton>
            </Flex>
          ))
        )}
      </Stack>
    </Stack>
  );
}
