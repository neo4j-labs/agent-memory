"use client";

import { Badge } from "@chakra-ui/react";
import { LuCheck, LuLoader, LuSprout, LuX } from "react-icons/lu";

/** success / failed / running (a trace whose outcome is not recorded yet). */
export function OutcomeBadge({ success }: { success: boolean | null }) {
  if (success === true) {
    return (
      <Badge size="sm" colorPalette="green">
        <LuCheck />
        success
      </Badge>
    );
  }
  if (success === false) {
    return (
      <Badge size="sm" colorPalette="red">
        <LuX />
        failed
      </Badge>
    );
  }
  return (
    <Badge size="sm" colorPalette="yellow">
      <LuLoader />
      running
    </Badge>
  );
}

/** Marks traces written by `make seed` rather than by a real agent turn. */
export function SeededBadge() {
  return (
    <Badge
      size="sm"
      variant="outline"
      colorPalette="gray"
      title="Written by the seed script to model earlier agent work"
    >
      <LuSprout />
      seeded
    </Badge>
  );
}
