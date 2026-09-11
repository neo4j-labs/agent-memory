"use client";

/**
 * "Why did it answer that?" — the reasoning steps recorded for this
 * conversation, read back with `reasoning.getTraceByConversation`.
 *
 * The chat route writes one step per answered turn in its `onEnd` callback, so
 * the drawer is an audit trail the app did not have to invent a store for.
 */

import {
  Badge,
  Button,
  Drawer,
  Portal,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useState } from "react";

import type { TracePayload } from "@/lib/types";

export function TraceDrawer({ conversationId }: { conversationId: string }) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<TracePayload | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `/api/memory/trace?conversationId=${encodeURIComponent(conversationId)}`,
      );
      if (response.ok) setTrace((await response.json()) as TracePayload);
    } catch {
      // Leave the previous trace on screen.
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  return (
    <Drawer.Root
      open={open}
      onOpenChange={(event) => {
        setOpen(event.open);
        if (event.open) void load();
      }}
      size="md"
    >
      <Drawer.Trigger asChild>
        <Button variant="outline" size="xs">
          Reasoning trace
        </Button>
      </Drawer.Trigger>
      <Portal>
        <Drawer.Backdrop />
        <Drawer.Positioner>
          <Drawer.Content>
            <Drawer.Header>
              <Drawer.Title>Reasoning trace</Drawer.Title>
            </Drawer.Header>
            <Drawer.Body>
              {loading && <Spinner />}
              {!loading && (trace?.steps.length ?? 0) === 0 && (
                <Text fontSize="sm" color="fg.muted">
                  No steps recorded yet — ask a question first.
                </Text>
              )}
              <Stack gap={4}>
                {trace?.steps.map((step, index) => (
                  <Stack
                    key={step.id}
                    gap={1}
                    borderWidth="1px"
                    borderRadius="md"
                    p={3}
                    bg="bg.subtle"
                  >
                    <Badge alignSelf="flex-start" variant="surface">
                      step {index + 1} · {step.actionTaken}
                    </Badge>
                    <Text fontSize="sm">{step.reasoning}</Text>
                    {step.result && (
                      <Text fontSize="xs" color="fg.muted" whiteSpace="pre-wrap">
                        {step.result}
                      </Text>
                    )}
                  </Stack>
                ))}
                {trace && trace.toolCalls.length > 0 && (
                  <Text fontSize="xs" color="fg.muted">
                    {trace.toolCalls.length} tool call(s) attached to this conversation.
                  </Text>
                )}
              </Stack>
            </Drawer.Body>
            <Drawer.CloseTrigger asChild>
              <Button variant="ghost" size="sm" m={3}>
                Close
              </Button>
            </Drawer.CloseTrigger>
          </Drawer.Content>
        </Drawer.Positioner>
      </Portal>
    </Drawer.Root>
  );
}
