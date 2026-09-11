"use client";

import {
  Box,
  Badge,
  Flex,
  Image,
  Link,
  Skeleton,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuExternalLink, LuHeart, LuTag } from "react-icons/lu";
import type { Entity, MemoryContext, Preference } from "@/lib/types";

const ENTITY_COLORS: Record<string, string> = {
  PERSON: "pink",
  ORGANIZATION: "orange",
  LOCATION: "blue",
  EVENT: "purple",
  OBJECT: "cyan",
};

function EntityRow({ entity }: { entity: Entity }) {
  const palette = ENTITY_COLORS[entity.type?.toUpperCase()] ?? "gray";
  const description = entity.enriched_description || entity.description;

  return (
    <Box
      p="2"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      bg="bg.subtle"
    >
      <Flex gap="2" alignItems="flex-start">
        {entity.image_url && (
          <Image
            src={entity.image_url}
            alt={entity.name}
            boxSize="32px"
            borderRadius="sm"
            objectFit="cover"
            flexShrink={0}
          />
        )}
        <Box flex="1" minW={0}>
          <Flex alignItems="center" gap="1" flexWrap="wrap">
            <Text fontSize="xs" fontWeight="semibold" lineClamp={1}>
              {entity.name}
            </Text>
            <Badge size="xs" variant="subtle" colorPalette={palette}>
              {entity.subtype || entity.type}
            </Badge>
          </Flex>
          {description && (
            <Text fontSize="xs" color="fg.muted" lineClamp={2} mt="0.5">
              {description}
            </Text>
          )}
          {entity.wikipedia_url && (
            <Link
              href={entity.wikipedia_url}
              target="_blank"
              rel="noopener noreferrer"
              fontSize="xs"
              color="blue.fg"
              mt="0.5"
            >
              Wikipedia <LuExternalLink size={10} />
            </Link>
          )}
        </Box>
      </Flex>
    </Box>
  );
}

function PreferenceRow({ preference }: { preference: Preference }) {
  return (
    <Flex gap="2" alignItems="flex-start">
      <Box color="purple.fg" mt="0.5">
        <LuHeart size={10} />
      </Box>
      <Box flex="1" minW={0}>
        <Text fontSize="xs">{preference.preference}</Text>
        <Text fontSize="xs" color="fg.muted">
          {preference.category}
        </Text>
      </Box>
    </Flex>
  );
}

interface LiveMemoryProps {
  context: MemoryContext;
  isLoading: boolean;
  error: string | null;
  hasThread: boolean;
}

/**
 * The live half of the memory panel: what the agent actually remembers about
 * this conversation, read from `GET /api/memory/context`.
 */
export function LiveMemory({
  context,
  isLoading,
  error,
  hasThread,
}: LiveMemoryProps) {
  if (!hasThread) {
    return (
      <Text fontSize="xs" color="fg.muted">
        Start a conversation to watch entities and preferences accumulate in the
        graph.
      </Text>
    );
  }

  if (isLoading && context.entities.length === 0) {
    return (
      <Stack gap="2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} height="44px" borderRadius="md" />
        ))}
      </Stack>
    );
  }

  if (error) {
    return (
      <Text fontSize="xs" color="red.fg">
        {error}
      </Text>
    );
  }

  const isEmpty =
    context.entities.length === 0 &&
    context.preferences.length === 0 &&
    context.recent_topics.length === 0;

  if (isEmpty) {
    return (
      <Text fontSize="xs" color="fg.muted">
        Nothing stored for this thread yet. Ask a question - extracted entities
        and detected preferences show up here.
      </Text>
    );
  }

  return (
    <Stack gap="3">
      {context.entities.length > 0 && (
        <Box>
          <Flex alignItems="center" gap="1" mb="1">
            <LuTag size={10} />
            <Text fontSize="xs" fontWeight="medium" color="fg.muted">
              Entities
            </Text>
            <Badge size="xs" ml="auto">
              {context.entities.length}
            </Badge>
          </Flex>
          <Stack gap="1.5">
            {context.entities.map((entity) => (
              <EntityRow key={entity.id} entity={entity} />
            ))}
          </Stack>
        </Box>
      )}

      {context.preferences.length > 0 && (
        <Box>
          <Flex alignItems="center" gap="1" mb="1">
            <LuHeart size={10} />
            <Text fontSize="xs" fontWeight="medium" color="fg.muted">
              Preferences
            </Text>
            <Badge size="xs" ml="auto">
              {context.preferences.length}
            </Badge>
          </Flex>
          <Stack gap="1.5">
            {context.preferences.map((preference) => (
              <PreferenceRow key={preference.id} preference={preference} />
            ))}
          </Stack>
        </Box>
      )}

      {context.recent_topics.length > 0 && (
        <Box>
          <Text fontSize="xs" fontWeight="medium" color="fg.muted" mb="1">
            Recent topics
          </Text>
          <Flex gap="1" flexWrap="wrap">
            {context.recent_topics.map((topic) => (
              <Badge key={topic} size="xs" variant="subtle">
                {topic}
              </Badge>
            ))}
          </Flex>
        </Box>
      )}
    </Stack>
  );
}
