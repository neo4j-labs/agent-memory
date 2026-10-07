"use client";

import {
  Badge,
  Box,
  Button,
  Flex,
  IconButton,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useMemo, useState } from "react";
import {
  LuArrowLeftRight,
  LuCheck,
  LuDatabase,
  LuGitMerge,
  LuRefreshCw,
  LuSparkles,
  LuX,
} from "react-icons/lu";
import { EntityBadge } from "@/components/ui/EntityBadge";
import { EmptyRow, LoadingRow, PanelSection } from "@/components/ui/PanelSection";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { useApi } from "@/hooks/useApi";
import { api, errorMessage } from "@/lib/api";
import { formatConfidence } from "@/lib/format";
import { fullType, labelColor, labelPalette, primaryLabel } from "@/lib/labels";
import type {
  MemoryEntity,
  MessageStoredEvent,
  PendingDuplicate,
} from "@/lib/types";

/** The support-desk ontology's labels first, anything else after. */
const GROUP_ORDER = [
  "Customer",
  "Order",
  "OrderLine",
  "Product",
  "Warranty",
  "WarrantyCoverage",
  "Policy",
];

interface MemoryPanelProps {
  threadId: string | null;
  /** Bumped by the page whenever memory may have changed. */
  refreshKey: number;
  /** The latest `message_stored` event of this session, for highlighting. */
  lastStored: (MessageStoredEvent & { threadId: string }) | null;
  /** Called after a review decision so the graph refetches too. */
  onMemoryChanged: () => void;
}

function groupEntities(entities: MemoryEntity[]) {
  const groups = new Map<string, MemoryEntity[]>();
  for (const entity of entities) {
    const label = primaryLabel(entity.labels, entity.type, GROUP_ORDER);
    const list = groups.get(label) ?? [];
    list.push(entity);
    groups.set(label, list);
  }
  const rank = (label: string) => {
    const index = GROUP_ORDER.indexOf(label);
    return index === -1 ? GROUP_ORDER.length : index;
  };
  return [...groups.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([label, items]) => ({
      label,
      items: [...items].sort(
        (a, b) => b.mentions - a.mentions || a.name.localeCompare(b.name),
      ),
    }));
}

function EntityRow({
  entity,
  highlighted,
}: {
  entity: MemoryEntity;
  highlighted: boolean;
}) {
  return (
    <Box
      px="3"
      py="2"
      borderRadius="md"
      bg={highlighted ? "green.subtle" : "bg.subtle"}
      borderWidth="1px"
      borderColor={highlighted ? "green.muted" : "transparent"}
      transition="background 0.3s"
    >
      <Flex alignItems="center" gap="2">
        <Text fontSize="sm" fontWeight="medium" flex="1" truncate>
          {entity.name}
        </Text>
        {highlighted ? (
          <Badge size="xs" colorPalette="green" variant="solid">
            <LuSparkles />
            new mention
          </Badge>
        ) : null}
        <Badge
          size="xs"
          variant="outline"
          title={`${entity.mentions} message(s) mention this entity`}
        >
          {entity.mentions} mention{entity.mentions === 1 ? "" : "s"}
        </Badge>
      </Flex>
      <Flex gap="2" mt="1" alignItems="center" flexWrap="wrap">
        <Text fontSize="xs" fontFamily="mono" color="fg.muted">
          {fullType(entity.type, entity.subtype)}
        </Text>
        <Text fontSize="xs" color="fg.subtle">
          :{entity.labels.join(":")}
        </Text>
      </Flex>
      {entity.aliases.length > 0 ? (
        <Text fontSize="xs" color="fg.muted" mt="1">
          also known as{" "}
          {entity.aliases.map((alias, i) => (
            <Text as="span" key={alias} fontStyle="italic">
              {i > 0 ? ", " : ""}
              {alias}
            </Text>
          ))}
        </Text>
      ) : null}
    </Box>
  );
}

function DuplicateCard({
  pair,
  busy,
  disabled,
  onReview,
}: {
  pair: PendingDuplicate;
  busy: boolean;
  disabled: boolean;
  onReview: (pair: PendingDuplicate, confirm: boolean) => void;
}) {
  const sourceLabel = primaryLabel(pair.source.labels, null, GROUP_ORDER);
  const targetLabel = primaryLabel(pair.target.labels, null, GROUP_ORDER);
  return (
    <Box
      p="3"
      borderRadius="md"
      borderWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
    >
      <Flex alignItems="center" gap="2" flexWrap="wrap">
        <EntityBadge name={pair.source.name} label={sourceLabel} />
        <LuArrowLeftRight aria-label="possibly the same as" />
        <EntityBadge name={pair.target.name} label={targetLabel} />
      </Flex>
      <Flex mt="2" gap="2" alignItems="center" flexWrap="wrap">
        <Badge colorPalette="yellow" size="sm">
          {formatConfidence(pair.confidence)} confidence
        </Badge>
        {pair.match_type ? (
          <Badge variant="outline" size="sm">
            {pair.match_type}
          </Badge>
        ) : null}
        <Flex gap="2" ml="auto">
          <Button
            size="xs"
            colorPalette="green"
            onClick={() => onReview(pair, true)}
            loading={busy}
            disabled={disabled}
            aria-label={`Confirm ${pair.source.name} and ${pair.target.name} are the same entity`}
          >
            <LuCheck />
            Confirm
          </Button>
          <Button
            size="xs"
            variant="outline"
            colorPalette="red"
            onClick={() => onReview(pair, false)}
            disabled={disabled || busy}
            aria-label={`Reject: ${pair.source.name} and ${pair.target.name} are different entities`}
          >
            <LuX />
            Reject
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
}

/**
 * Long-term memory for the current thread: the entities its messages
 * mention, grouped by ontology label, and the `SAME_AS` pairs entity
 * resolution put in the review band.
 */
export function MemoryPanel({
  threadId,
  refreshKey,
  lastStored,
  onMemoryChanged,
}: MemoryPanelProps) {
  const load = useCallback(
    (signal: AbortSignal) => api.memory.context(threadId, { signal }),
    [threadId],
  );
  const { data, error, loading, reload } = useApi(load, refreshKey);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const groups = useMemo(() => groupEntities(data?.entities ?? []), [data]);

  const justMentioned = useMemo(() => {
    if (!lastStored || lastStored.threadId !== threadId) return new Set<string>();
    return new Set(lastStored.entities.map((e) => e.name.toLowerCase()));
  }, [lastStored, threadId]);

  const review = useCallback(
    async (pair: PendingDuplicate, confirm: boolean) => {
      const key = `${pair.source.id}|${pair.target.id}`;
      setBusyKey(key);
      setReviewError(null);
      try {
        await api.memory.reviewDuplicate(pair.source.id, pair.target.id, confirm);
        onMemoryChanged();
        reload();
      } catch (err) {
        setReviewError(errorMessage(err));
      } finally {
        setBusyKey(null);
      }
    },
    [onMemoryChanged, reload],
  );

  const entityCount = data?.entities.length ?? 0;
  const pending = data?.pending_duplicates ?? [];

  return (
    <Stack gap="6">
      <PanelSection
        title="Entities"
        icon={<LuDatabase size={16} />}
        description={
          threadId
            ? "Entities this conversation's messages mention, typed by the active ontology."
            : "No conversation selected. Pending review below covers the whole graph."
        }
        actions={
          <Flex alignItems="center" gap="1">
            {data ? (
              <Badge size="sm" variant="subtle">
                {entityCount}
              </Badge>
            ) : null}
            <IconButton
              aria-label="Refresh memory"
              size="xs"
              variant="ghost"
              onClick={reload}
              disabled={loading}
            >
              {loading ? <Spinner size="xs" /> : <LuRefreshCw />}
            </IconButton>
          </Flex>
        }
      >
        {lastStored && lastStored.threadId === threadId ? (
          <Box
            p="2"
            borderRadius="md"
            bg="green.subtle"
            fontSize="xs"
            aria-live="polite"
          >
            <Text fontWeight="medium" mb="1">
              Last message stored — extraction found {lastStored.entities.length}{" "}
              entit{lastStored.entities.length === 1 ? "y" : "ies"}
            </Text>
            <Flex gap="1" flexWrap="wrap">
              {lastStored.entities.map((entity) => (
                <EntityBadge
                  key={`${entity.type}:${entity.name}`}
                  name={entity.name}
                  label={primaryLabel(entity.labels, entity.type, GROUP_ORDER)}
                />
              ))}
            </Flex>
          </Box>
        ) : null}

        {error ? (
          <StatusAlert
            status="error"
            title="Memory unavailable"
            description={error}
          />
        ) : null}

        {!data && loading ? (
          <LoadingRow label="Loading memory…" />
        ) : data && entityCount === 0 ? (
          <EmptyRow>
            No entities yet. Mention a customer, an order (ORD-…), an order line
            (ITEM-…), a product or a warranty (WRT-…) and extraction will type it
            with the ontology.
          </EmptyRow>
        ) : (
          <Stack gap="4">
            {groups.map((group) => (
              <Stack key={group.label} gap="2">
                <Flex alignItems="center" gap="2">
                  <Box
                    w="3"
                    h="3"
                    borderRadius="full"
                    style={{ background: labelColor(group.label) }}
                  />
                  <Text fontSize="sm" fontWeight="semibold">
                    {group.label}
                  </Text>
                  <Badge
                    size="xs"
                    colorPalette={labelPalette(group.label)}
                    variant="subtle"
                  >
                    {group.items.length}
                  </Badge>
                </Flex>
                <Stack gap="1">
                  {group.items.map((entity) => (
                    <EntityRow
                      key={entity.id}
                      entity={entity}
                      highlighted={justMentioned.has(entity.name.toLowerCase())}
                    />
                  ))}
                </Stack>
              </Stack>
            ))}
          </Stack>
        )}
      </PanelSection>

      <PanelSection
        title="Pending review"
        icon={<LuGitMerge size={16} />}
        description="Pairs entity resolution scored in the review band. Confirm merges them; reject keeps them apart."
        actions={
          data ? (
            <Badge
              size="sm"
              colorPalette={pending.length > 0 ? "yellow" : "gray"}
            >
              {pending.length}
            </Badge>
          ) : null
        }
      >
        {reviewError ? (
          <StatusAlert
            status="error"
            title="Review failed"
            description={reviewError}
            onDismiss={() => setReviewError(null)}
          />
        ) : null}
        {!data ? null : pending.length === 0 ? (
          <EmptyRow>Nothing waiting for review.</EmptyRow>
        ) : (
          <Stack gap="2">
            {pending.map((pair) => {
              const key = `${pair.source.id}|${pair.target.id}`;
              return (
                <DuplicateCard
                  key={key}
                  pair={pair}
                  busy={busyKey === key}
                  disabled={busyKey !== null}
                  onReview={review}
                />
              );
            })}
          </Stack>
        )}
      </PanelSection>
    </Stack>
  );
}
