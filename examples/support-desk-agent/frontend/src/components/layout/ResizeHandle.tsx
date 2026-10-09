"use client";

import { Box } from "@chakra-ui/react";
import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { clamp } from "@/hooks/useResizableWidth";

/** Pixels per arrow-key press; Shift moves four times as far. */
const KEY_STEP = 16;

interface ResizeHandleProps {
  /** Which edge of its column the handle sits on. */
  edge: "start" | "end";
  /** Accessible name, e.g. "Resize the conversations sidebar". */
  label: string;
  width: number;
  min: number;
  max: number;
  /** True while a drag is in progress (keeps the edge highlighted). */
  dragging: boolean;
  /** Live width while dragging. */
  onPreview: (width: number) => void;
  /** Final width: end of a drag, or a keyboard step. */
  onCommit: (width: number) => void;
  /** Double-click: back to the default width. */
  onReset: () => void;
}

/**
 * A draggable column edge (a WAI-ARIA window splitter).
 *
 * Drag it with a mouse, pen or finger; focus it and use the arrow keys
 * (Shift for bigger steps), Home and End; double-click to reset. The pointer
 * is captured for the whole drag, so moving over the graph canvas or leaving
 * the window does not drop it.
 */
export function ResizeHandle({
  edge,
  label,
  width,
  min,
  max,
  dragging,
  onPreview,
  onCommit,
  onReset,
}: ResizeHandleProps) {
  const drag = useRef<{ x: number; width: number; last: number } | null>(null);
  // A column on the left grows when its end edge moves right; one on the
  // right grows when its start edge moves left.
  const direction = edge === "end" ? 1 : -1;

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, width, last: width };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start) return;
    const next = clamp(
      start.width + direction * (event.clientX - start.x),
      min,
      max,
    );
    start.last = next;
    onPreview(next);
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    onCommit(start.last);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = (event.shiftKey ? 4 : 1) * KEY_STEP;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = width + direction * step;
    else if (event.key === "ArrowLeft") next = width - direction * step;
    else if (event.key === "Home") next = min;
    else if (event.key === "End") next = max;
    if (next === null) return;
    event.preventDefault();
    onCommit(clamp(next, min, max));
  };

  return (
    <Box
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      aria-valuemin={Math.round(min)}
      aria-valuemax={Math.round(max)}
      title={`${label} — drag, or use the arrow keys; double-click to reset`}
      tabIndex={0}
      position="absolute"
      top="0"
      bottom="0"
      {...(edge === "end" ? { right: "-4px" } : { left: "-4px" })}
      w="8px"
      zIndex="2"
      cursor="col-resize"
      touchAction="none"
      display="flex"
      justifyContent="center"
      outline="none"
      css={{
        "& > span": { transition: "background 0.15s, width 0.15s" },
        "&:hover > span, &:focus-visible > span, &[data-dragging] > span": {
          background: "var(--chakra-colors-brand-solid)",
          width: "3px",
        },
      }}
      data-dragging={dragging ? "" : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
    >
      <Box as="span" w="0" h="full" />
    </Box>
  );
}
