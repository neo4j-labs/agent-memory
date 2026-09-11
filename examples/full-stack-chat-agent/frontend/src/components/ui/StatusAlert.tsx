"use client";

import { Alert, CloseButton } from "@chakra-ui/react";

interface StatusAlertProps {
  status: "error" | "warning" | "info" | "success";
  title: string;
  description?: string | null;
  onDismiss?: () => void;
}

/**
 * One-line Chakra v3 alert used for every user-visible failure in this app.
 *
 * The previous version of this example kept `error` state in `useChat` and
 * `useThreads` that nothing ever rendered, so a dead backend looked exactly
 * like an empty database.
 */
export function StatusAlert({
  status,
  title,
  description,
  onDismiss,
}: StatusAlertProps) {
  return (
    <Alert.Root status={status} size="sm" borderRadius="md">
      <Alert.Indicator />
      <Alert.Content>
        <Alert.Title>{title}</Alert.Title>
        {description ? (
          <Alert.Description>{description}</Alert.Description>
        ) : null}
      </Alert.Content>
      {onDismiss ? (
        <CloseButton
          aria-label="Dismiss"
          size="xs"
          variant="ghost"
          onClick={onDismiss}
        />
      ) : null}
    </Alert.Root>
  );
}
