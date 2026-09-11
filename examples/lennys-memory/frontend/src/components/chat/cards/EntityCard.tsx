"use client";

import {
  Box,
  Flex,
  Text,
  Badge,
  Image,
  Link,
  VStack,
  HStack,
  Grid,
  Portal,
  CloseButton,
} from "@chakra-ui/react";
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogBody,
  DialogCloseTrigger,
  DialogBackdrop,
  DialogPositioner,
} from "@chakra-ui/react";
import { BaseCard } from "./BaseCard";
import {
  LuUser,
  LuBuilding2,
  LuMapPin,
  LuCalendar,
  LuBox,
  LuExternalLink,
  LuBookOpen,
  LuMessageSquare,
  LuLink,
  LuLightbulb,
  LuPackage,
} from "react-icons/lu";
import type { EntityCardProps } from "./types";

/**
 * Get icon for entity type
 */
function getEntityIcon(type: string) {
  const t = type.toUpperCase();
  switch (t) {
    case "PERSON":
      return <LuUser size={16} />;
    case "ORGANIZATION":
      return <LuBuilding2 size={16} />;
    case "LOCATION":
      return <LuMapPin size={16} />;
    case "EVENT":
      return <LuCalendar size={16} />;
    case "CONCEPT":
    case "TOPIC":
      return <LuLightbulb size={16} />;
    case "OBJECT":
      return <LuPackage size={16} />;
    default:
      return <LuBox size={16} />;
  }
}

/**
 * Get color palette for entity type
 */
function getEntityColor(type: string): string {
  const t = type.toUpperCase();
  switch (t) {
    case "PERSON":
      return "pink";
    case "ORGANIZATION":
      return "orange";
    case "LOCATION":
      return "blue";
    case "EVENT":
      return "purple";
    case "CONCEPT":
    case "TOPIC":
      return "green";
    case "OBJECT":
      return "cyan";
    default:
      return "gray";
  }
}

/**
 * EntityCard displays a rich knowledge panel for an entity.
 *
 * Features:
 * - Wikipedia-style knowledge panel layout
 * - Entity image (if enriched)
 * - Description from Wikipedia
 * - Quick facts (type, subtype, mentions)
 * - Wikipedia link
 * - Related entities (if available)
 * - Podcast mentions
 */
export function EntityCard({
  toolCall,
  entity,
  mentions = [],
  relatedEntities = [],
  isExpanded = false,
  onExpand,
  onCollapse,
  compactHeight = 280,
}: EntityCardProps) {
  const hasEnrichment = !!(
    entity.enriched_description ||
    entity.wikipedia_url ||
    entity.image_url
  );

  const colorPalette = getEntityColor(entity.type);
  const icon = getEntityIcon(entity.type);

  // Compact content for inline display
  const compactContent = (
    <Flex gap={3} height="100%">
      {/* Image column */}
      {entity.image_url ? (
        <Box flexShrink={0} width="100px">
          <Image
            src={entity.image_url}
            alt={entity.name}
            borderRadius="md"
            objectFit="cover"
            width="100px"
            height="100px"
          />
        </Box>
      ) : (
        <Box
          flexShrink={0}
          width="100px"
          height="100px"
          bg="bg.muted"
          borderRadius="md"
          display="flex"
          alignItems="center"
          justifyContent="center"
        >
          {icon}
        </Box>
      )}

      {/* Content column */}
      <VStack align="start" flex={1} gap={2} overflow="hidden">
        {/* Entity name and type */}
        <Box>
          <Text fontWeight="bold" fontSize="md" lineClamp={1}>
            {entity.name}
          </Text>
          <HStack gap={1} mt={1}>
            <Badge colorPalette={colorPalette} size="sm">
              {entity.type}
            </Badge>
            {entity.subtype && (
              <Badge variant="subtle" size="sm">
                {entity.subtype}
              </Badge>
            )}
            {hasEnrichment && (
              <Badge colorPalette="purple" variant="subtle" size="sm">
                Enriched
              </Badge>
            )}
          </HStack>
        </Box>

        {/* Description - with fallback to mention content */}
        <Text fontSize="sm" color="fg.muted" lineClamp={3}>
          {entity.enriched_description ||
            entity.description ||
            (mentions.length > 0 && mentions[0].content
              ? `Mentioned in podcast: "${mentions[0].content.slice(0, 150)}${mentions[0].content.length > 150 ? "..." : ""}"`
              : "No description available")}
        </Text>

        {/* Related entities preview - show top 3 */}
        {relatedEntities.length > 0 && (
          <HStack gap={1} flexWrap="wrap">
            <LuLink size={12} style={{ flexShrink: 0 }} />
            {relatedEntities.slice(0, 3).map((rel, idx) => (
              <Badge
                key={idx}
                size="xs"
                variant="subtle"
                colorPalette={getEntityColor(rel.type)}
              >
                {rel.name}
              </Badge>
            ))}
            {relatedEntities.length > 3 && (
              <Text fontSize="xs" color="fg.muted">
                +{relatedEntities.length - 3}
              </Text>
            )}
          </HStack>
        )}

        {/* Quick stats */}
        <HStack gap={3} fontSize="xs" color="fg.muted">
          {mentions.length > 0 && (
            <HStack gap={1}>
              <LuMessageSquare size={12} />
              <Text>{mentions.length} mentions</Text>
            </HStack>
          )}
        </HStack>

        {/* Wikipedia link */}
        {entity.wikipedia_url && (
          <Link
            href={entity.wikipedia_url}
            target="_blank"
            rel="noopener noreferrer"
            fontSize="xs"
            color="teal.fg"
            display="flex"
            alignItems="center"
            gap={1}
          >
            <LuBookOpen size={12} />
            Wikipedia
            <LuExternalLink size={10} />
          </Link>
        )}
      </VStack>
    </Flex>
  );

  // Expanded content for fullscreen dialog
  const expandedContent = (
    <Grid
      templateColumns={{ base: "1fr", md: "250px 1fr" }}
      gap={6}
      height="100%"
    >
      {/* Left sidebar - Image and quick facts */}
      <VStack align="stretch" gap={4}>
        {/* Image */}
        {entity.image_url ? (
          <Image
            src={entity.image_url}
            alt={entity.name}
            borderRadius="lg"
            objectFit="cover"
            width="100%"
            maxH="250px"
          />
        ) : (
          <Box
            bg="bg.muted"
            borderRadius="lg"
            height="200px"
            display="flex"
            alignItems="center"
            justifyContent="center"
            fontSize="4xl"
            color="fg.muted"
          >
            {icon}
          </Box>
        )}

        {/* Quick facts */}
        <Box bg="bg.subtle" borderRadius="md" p={3}>
          <Text fontWeight="semibold" fontSize="sm" mb={2}>
            Quick Facts
          </Text>
          <VStack align="stretch" gap={2} fontSize="sm">
            <Flex justify="space-between">
              <Text color="fg.muted">Type</Text>
              <Badge colorPalette={colorPalette}>{entity.type}</Badge>
            </Flex>
            {entity.subtype && (
              <Flex justify="space-between">
                <Text color="fg.muted">Subtype</Text>
                <Text>{entity.subtype}</Text>
              </Flex>
            )}
            {mentions.length > 0 && (
              <Flex justify="space-between">
                <Text color="fg.muted">Mentions</Text>
                <Text>{mentions.length}</Text>
              </Flex>
            )}
            {entity.wikidata_id && (
              <Flex justify="space-between">
                <Text color="fg.muted">Wikidata</Text>
                <Link
                  href={`https://www.wikidata.org/wiki/${entity.wikidata_id}`}
                  target="_blank"
                  color="teal.fg"
                  fontSize="xs"
                >
                  {entity.wikidata_id}
                </Link>
              </Flex>
            )}
          </VStack>
        </Box>

        {/* Wikipedia link */}
        {entity.wikipedia_url && (
          <Link
            href={entity.wikipedia_url}
            target="_blank"
            rel="noopener noreferrer"
            display="flex"
            alignItems="center"
            justifyContent="center"
            gap={2}
            bg="blue.subtle"
            color="blue.fg"
            borderRadius="md"
            p={2}
            fontWeight="medium"
            fontSize="sm"
            _hover={{ bg: "blue.muted" }}
          >
            <LuBookOpen size={16} />
            View on Wikipedia
            <LuExternalLink size={14} />
          </Link>
        )}
      </VStack>

      {/* Right content - Description, mentions, related */}
      <VStack align="stretch" gap={4} overflow="auto">
        {/* Entity header */}
        <Box>
          <Text fontSize="2xl" fontWeight="bold">
            {entity.name}
          </Text>
          <HStack gap={2} mt={1}>
            <Badge colorPalette={colorPalette} size="md">
              {entity.type}
            </Badge>
            {entity.subtype && (
              <Badge variant="outline" size="md">
                {entity.subtype}
              </Badge>
            )}
            {hasEnrichment && (
              <Badge colorPalette="purple" size="md">
                Wikipedia Enriched
              </Badge>
            )}
          </HStack>
        </Box>

        {/* Description */}
        <Box>
          <Text fontWeight="semibold" mb={2}>
            Description
          </Text>
          <Text color="fg.muted" lineHeight="tall">
            {entity.enriched_description ||
              entity.description ||
              "No description available."}
          </Text>
        </Box>

        {/* Related entities */}
        {relatedEntities.length > 0 && (
          <Box>
            <Text fontWeight="semibold" mb={2}>
              Related Entities
            </Text>
            <Flex gap={2} flexWrap="wrap">
              {relatedEntities.slice(0, 10).map((related, idx) => (
                <Badge
                  key={idx}
                  colorPalette={getEntityColor(related.type)}
                  variant="subtle"
                  size="sm"
                >
                  {related.name}
                </Badge>
              ))}
              {relatedEntities.length > 10 && (
                <Badge variant="outline" size="sm">
                  +{relatedEntities.length - 10} more
                </Badge>
              )}
            </Flex>
          </Box>
        )}

        {/* Podcast mentions */}
        {mentions.length > 0 && (
          <Box>
            <Text fontWeight="semibold" mb={2}>
              Mentioned In
            </Text>
            <VStack align="stretch" gap={2} maxH="300px" overflow="auto">
              {mentions.slice(0, 10).map((mention, idx) => (
                <Box
                  key={idx}
                  bg="bg.subtle"
                  borderRadius="md"
                  p={3}
                  fontSize="sm"
                >
                  <Text fontWeight="medium" mb={1}>
                    {mention.speaker || "Unknown Speaker"}
                  </Text>
                  <Text color="fg.muted" lineClamp={3}>
                    &quot;{mention.content}&quot;
                  </Text>
                  {mention.episode && (
                    <Text fontSize="xs" color="fg.subtle" mt={1}>
                      Episode: {mention.episode}
                    </Text>
                  )}
                </Box>
              ))}
              {mentions.length > 10 && (
                <Text fontSize="sm" color="fg.muted" textAlign="center">
                  +{mentions.length - 10} more mentions
                </Text>
              )}
            </VStack>
          </Box>
        )}
      </VStack>
    </Grid>
  );

  // Fullscreen dialog
  if (isExpanded) {
    return (
      <DialogRoot open={true} size="cover" onOpenChange={() => onCollapse?.()}>
        <Portal>
          <DialogBackdrop />
          <DialogPositioner>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  <Flex align="center" gap={2}>
                    {icon}
                    {entity.name}
                    {hasEnrichment && (
                      <Badge colorPalette="purple" size="sm">
                        Enriched
                      </Badge>
                    )}
                  </Flex>
                </DialogTitle>
                <DialogCloseTrigger asChild>
                  <CloseButton size="sm" />
                </DialogCloseTrigger>
              </DialogHeader>
              <DialogBody p={6} height="calc(100vh - 80px)" overflow="auto">
                {expandedContent}
              </DialogBody>
            </DialogContent>
          </DialogPositioner>
        </Portal>
      </DialogRoot>
    );
  }

  return (
    <BaseCard
      toolCall={toolCall}
      title={entity.name}
      icon={icon}
      colorPalette={colorPalette}
      isExpanded={isExpanded}
      onExpand={onExpand}
      onCollapse={onCollapse}
      compactHeight={compactHeight}
      footer={
        hasEnrichment
          ? "Wikipedia enriched"
          : mentions.length > 0
            ? `${mentions.length} mentions`
            : undefined
      }
    >
      {compactContent}
    </BaseCard>
  );
}
