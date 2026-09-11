"use client";

import { useRef, useState, useEffect } from "react";
import {
  Box,
  Table,
  Text,
  VStack,
  Portal,
  CloseButton,
  Flex,
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
import { LuChevronRight } from "react-icons/lu";
import { BaseCard } from "./BaseCard";
import { LuTable } from "react-icons/lu";
import type { DataCardProps } from "./types";

/**
 * Format a cell value for display
 */
function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") {
    return value.length > 100 ? `${value.slice(0, 100)}...` : value;
  }
  if (typeof value === "number") {
    // Format confidence scores as percentages
    if (value > 0 && value <= 1) {
      return `${(value * 100).toFixed(0)}%`;
    }
    return value.toLocaleString();
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    return value.length > 0 ? `${value.length} items` : "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value).slice(0, 50) + "...";
  }
  return String(value);
}

/**
 * DataCard displays tabular data from search results.
 * Features:
 * - Auto-detected columns based on tool type
 * - Compact mode: limited rows with "more" indicator
 * - Horizontal scroll on mobile
 * - Expand to fullscreen for full dataset
 */
export function DataCard({
  toolCall,
  columns,
  rows,
  title,
  compactRowLimit = 5,
  isExpanded = false,
  onExpand,
  onCollapse,
  compactHeight = 220,
}: DataCardProps) {
  const displayRows = isExpanded ? rows : rows.slice(0, compactRowLimit);
  const hasMore = rows.length > compactRowLimit;
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [canScrollRight, setCanScrollRight] = useState(false);

  // Check if table can scroll horizontally
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (container) {
      const checkScroll = () => {
        setCanScrollRight(
          container.scrollWidth > container.clientWidth &&
            container.scrollLeft <
              container.scrollWidth - container.clientWidth - 10,
        );
      };
      checkScroll();
      container.addEventListener("scroll", checkScroll);
      window.addEventListener("resize", checkScroll);
      return () => {
        container.removeEventListener("scroll", checkScroll);
        window.removeEventListener("resize", checkScroll);
      };
    }
  }, [rows, columns]);

  const cardTitle = title || `Results (${rows.length})`;

  const tableContent = (
    <Box height="100%" position="relative">
      {rows.length === 0 ? (
        <VStack height="100%" justify="center" p={4}>
          <Text fontSize="sm" color="fg.muted">
            No results found
          </Text>
        </VStack>
      ) : (
        <>
          <Box
            ref={scrollContainerRef}
            overflowX="auto"
            overflowY="auto"
            height="100%"
            css={{
              "&::-webkit-scrollbar": { height: "6px", width: "6px" },
              "&::-webkit-scrollbar-track": { background: "transparent" },
              "&::-webkit-scrollbar-thumb": {
                background: "var(--chakra-colors-gray-300)",
                borderRadius: "3px",
              },
            }}
          >
            <Table.Root size="sm" striped>
              <Table.Header>
                <Table.Row>
                  {columns.map((col) => (
                    <Table.ColumnHeader
                      key={col.key}
                      width={col.width}
                      fontSize="xs"
                      fontWeight="semibold"
                      whiteSpace="nowrap"
                      px={2}
                      py={2}
                    >
                      {col.label}
                    </Table.ColumnHeader>
                  ))}
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {displayRows.map((row, idx) => (
                  <Table.Row key={idx}>
                    {columns.map((col) => (
                      <Table.Cell
                        key={col.key}
                        fontSize="xs"
                        py={2}
                        px={2}
                        maxW="300px"
                        overflow="hidden"
                        textOverflow="ellipsis"
                      >
                        {col.render
                          ? col.render(row[col.key], row)
                          : formatCellValue(row[col.key])}
                      </Table.Cell>
                    ))}
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          </Box>
          {/* Scroll indicator */}
          {canScrollRight && !isExpanded && (
            <Flex
              position="absolute"
              right={0}
              top={0}
              bottom={hasMore ? "32px" : 0}
              width="32px"
              alignItems="center"
              justifyContent="center"
              bg="linear-gradient(to right, transparent, var(--chakra-colors-bg-panel))"
              pointerEvents="none"
            >
              <Box color="fg.muted" animation="pulse 1.5s infinite">
                <LuChevronRight size={16} />
              </Box>
            </Flex>
          )}
          {!isExpanded && hasMore && (
            <Box
              p={2}
              textAlign="center"
              bg="bg.muted"
              borderTopWidth="1px"
              borderColor="border.subtle"
            >
              <Text fontSize="xs" color="fg.muted">
                +{rows.length - compactRowLimit} more results. Click expand to
                see all.
              </Text>
            </Box>
          )}
        </>
      )}
    </Box>
  );

  // Footer summary
  const footerText = `${rows.length} result${rows.length !== 1 ? "s" : ""}`;

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
                    <LuTable />
                    {cardTitle}
                  </Flex>
                </DialogTitle>
                <DialogCloseTrigger asChild>
                  <CloseButton size="sm" />
                </DialogCloseTrigger>
              </DialogHeader>
              <DialogBody p={4} height="calc(100vh - 80px)" overflow="auto">
                {tableContent}
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
      title={cardTitle}
      icon={<LuTable size={14} />}
      colorPalette="green"
      isExpanded={isExpanded}
      onExpand={onExpand}
      onCollapse={onCollapse}
      compactHeight={compactHeight}
      footer={footerText}
    >
      {tableContent}
    </BaseCard>
  );
}
