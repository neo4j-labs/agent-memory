"use client";

import { Badge } from "@chakra-ui/react";

/**
 * Neo4j Labs lifecycle badge.
 *
 * Labs projects declare their maturity in the UI as well as the README —
 * see "Conventions across examples" in `examples/README.md`.
 */
export function LabsBadge() {
  return (
    <Badge
      colorPalette="brand"
      variant="subtle"
      size="sm"
      textTransform="uppercase"
      letterSpacing="wide"
    >
      Beta
    </Badge>
  );
}
