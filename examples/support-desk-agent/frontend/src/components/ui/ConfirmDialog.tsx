"use client";

import { Button, CloseButton, Dialog, Portal } from "@chakra-ui/react";
import type { ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  confirmPalette?: string;
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** Chakra v3 `Dialog` used as an accessible confirm prompt. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  confirmPalette = "brand",
  loading = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(details) => {
        if (!details.open && !loading) onClose();
      }}
      role="alertdialog"
      placement="center"
      lazyMount
      unmountOnExit
    >
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content mx="4">
            <Dialog.Header>
              <Dialog.Title>{title}</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body fontSize="sm">{children}</Dialog.Body>
            <Dialog.Footer>
              <Dialog.ActionTrigger asChild>
                <Button variant="outline" size="sm" disabled={loading}>
                  Cancel
                </Button>
              </Dialog.ActionTrigger>
              <Button
                size="sm"
                colorPalette={confirmPalette}
                loading={loading}
                onClick={onConfirm}
              >
                {confirmLabel}
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild disabled={loading}>
              <CloseButton size="sm" />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
}
