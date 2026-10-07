"use client";

import { Badge, Box } from "@chakra-ui/react";
import { labelColor, labelPalette } from "@/lib/labels";

interface EntityBadgeProps {
  name: string;
  /** The display label (ontology label or POLE+O label). */
  label: string;
  title?: string;
  highlighted?: boolean;
}

/** `● Priya Patel · Customer`, coloured like the node in the graph. */
export function EntityBadge({
  name,
  label,
  title,
  highlighted = false,
}: EntityBadgeProps) {
  return (
    <Badge
      size="sm"
      variant={highlighted ? "solid" : "subtle"}
      colorPalette={labelPalette(label)}
      title={title ?? `${name} (${label})`}
      display="inline-flex"
      alignItems="center"
      gap="1"
      maxW="full"
    >
      <Box
        as="span"
        w="2"
        h="2"
        borderRadius="full"
        flexShrink={0}
        style={{ background: labelColor(label) }}
      />
      <Box as="span" truncate>
        {name}
      </Box>
      <Box as="span" opacity={0.7} fontWeight="normal">
        · {label}
      </Box>
    </Badge>
  );
}
