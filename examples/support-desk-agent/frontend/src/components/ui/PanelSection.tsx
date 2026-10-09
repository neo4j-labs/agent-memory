"use client";

import { Flex, Heading, Spinner, Stack, Text } from "@chakra-ui/react";
import type { ReactNode } from "react";

interface PanelSectionProps {
  title: string;
  icon?: ReactNode;
  /** Right-aligned controls (refresh button, counts). */
  actions?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}

/** A titled block inside one of the right-hand panels. */
export function PanelSection({
  title,
  icon,
  actions,
  description,
  children,
}: PanelSectionProps) {
  return (
    <Stack as="section" gap="2">
      <Flex alignItems="center" gap="2" minH="7">
        {icon}
        <Heading as="h3" size="sm" flex="1">
          {title}
        </Heading>
        {actions}
      </Flex>
      {description ? (
        <Text fontSize="xs" color="fg.muted">
          {description}
        </Text>
      ) : null}
      {children}
    </Stack>
  );
}

export function LoadingRow({ label = "Loading…" }: { label?: string }) {
  return (
    <Flex alignItems="center" gap="2" color="fg.muted" fontSize="sm" py="2">
      <Spinner size="xs" />
      <Text>{label}</Text>
    </Flex>
  );
}

export function EmptyRow({ children }: { children: ReactNode }) {
  return (
    <Text fontSize="sm" color="fg.muted" py="2">
      {children}
    </Text>
  );
}
