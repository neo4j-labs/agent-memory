"use client";

import {
  Badge,
  Box,
  Collapsible,
  Flex,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuChevronRight, LuWrench } from "react-icons/lu";
import { EntityBadge } from "@/components/ui/EntityBadge";
import { JsonBlock } from "@/components/ui/JsonBlock";
import type { ToolCallState } from "@/hooks/useChat";
import { formatDuration, preview } from "@/lib/format";
import { primaryLabel } from "@/lib/labels";

const STATUS_PALETTE: Record<ToolCallState["status"], string> = {
  pending: "yellow",
  success: "green",
  error: "red",
};

/**
 * One tool call of an assistant turn: name, status and duration in the
 * header, the entities it touched (the TOUCHED audit edges the backend
 * records) always visible, and arguments/result behind a disclosure.
 */
export function ToolCallCard({ call }: { call: ToolCallState }) {
  const argsPreview = preview(call.args, 80);
  return (
    <Collapsible.Root>
      <Box
        borderWidth="1px"
        borderColor="border.subtle"
        borderRadius="md"
        overflow="hidden"
        bg="bg.panel"
      >
        <Collapsible.Trigger asChild>
          <Flex
            as="button"
            w="full"
            px="3"
            py="2"
            gap="2"
            alignItems="center"
            bg="bg.muted"
            textAlign="left"
            cursor="pointer"
            _hover={{ bg: "bg.emphasized" }}
            css={{
              "&[data-state=open] .chevron": { transform: "rotate(90deg)" },
            }}
          >
            <Box className="chevron" transition="transform 0.15s">
              <LuChevronRight size={14} />
            </Box>
            <LuWrench size={14} />
            <Text fontSize="sm" fontWeight="medium" fontFamily="mono">
              {call.name}
            </Text>
            <Text
              fontSize="xs"
              color="fg.muted"
              flex="1"
              truncate
              fontFamily="mono"
            >
              {argsPreview === "{}" ? "" : argsPreview}
            </Text>
            {call.status === "pending" ? <Spinner size="xs" /> : null}
            <Badge colorPalette={STATUS_PALETTE[call.status]} size="sm">
              {call.status}
            </Badge>
            {call.durationMs !== undefined ? (
              <Text fontSize="xs" color="fg.muted" flexShrink={0}>
                {formatDuration(call.durationMs)}
              </Text>
            ) : null}
          </Flex>
        </Collapsible.Trigger>

        {call.touched.length > 0 ? (
          <Flex px="3" py="2" gap="1" flexWrap="wrap" alignItems="center">
            <Text fontSize="xs" color="fg.muted" mr="1">
              Touched
            </Text>
            {call.touched.map((ref) => {
              const label = primaryLabel(ref.labels, ref.type);
              return (
                <EntityBadge
                  key={ref.id || `${ref.type}:${ref.name}`}
                  name={ref.name}
                  label={label}
                  title={`${ref.name} (${label}) — recorded as a TOUCHED edge`}
                />
              );
            })}
          </Flex>
        ) : null}

        <Collapsible.Content>
          <Stack p="3" gap="3">
            <JsonBlock label="Arguments" value={call.args} />
            {call.result !== undefined ? (
              <JsonBlock label="Result" value={call.result} maxH="260px" />
            ) : (
              <Text fontSize="xs" color="fg.muted">
                Waiting for the result…
              </Text>
            )}
          </Stack>
        </Collapsible.Content>
      </Box>
    </Collapsible.Root>
  );
}
