---
name: excalidraw
description: Generate Excalidraw JSON format diagrams for documentation, architecture diagrams, flowcharts, entity relationships, and visual explanations. Use when users request Excalidraw diagrams, .excalidraw files, visual diagrams for docs, architecture visualizations, flowcharts, or when converting ASCII art/placeholder diagrams to proper Excalidraw format.
---

# Excalidraw Diagram Generator

Generate valid Excalidraw JSON for diagrams that can be opened in Excalidraw, embedded in documentation, or used in Obsidian.

## JSON Schema Structure

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [],
  "appState": {
    "gridSize": 20,
    "viewBackgroundColor": "#ffffff"
  },
  "files": {}
}
```

## Element Types

### Base Properties (all elements)

```json
{
  "id": "unique-id",
  "type": "rectangle|ellipse|diamond|text|arrow|line",
  "x": 0,
  "y": 0,
  "width": 100,
  "height": 50,
  "angle": 0,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "transparent",
  "fillStyle": "solid",
  "strokeWidth": 2,
  "strokeStyle": "solid",
  "roughness": 1,
  "opacity": 100,
  "seed": 12345,
  "version": 1,
  "isDeleted": false,
  "groupIds": [],
  "frameId": null,
  "roundness": { "type": 3 },
  "boundElements": null
}
```

### Shape Elements: rectangle, ellipse, diamond

Use for boxes, containers, nodes. Diamond for decision points.

### Text Elements

```json
{
  "type": "text",
  "text": "Label",
  "fontSize": 20,
  "fontFamily": 1,
  "textAlign": "center",
  "verticalAlign": "middle",
  "containerId": null
}
```

`fontFamily`: 1=Virgil (hand-drawn), 2=Helvetica, 3=Cascadia (code)

### Linear Elements: arrow, line

```json
{
  "type": "arrow",
  "points": [[0, 0], [100, 0]],
  "startBinding": null,
  "endBinding": null,
  "startArrowhead": null,
  "endArrowhead": "arrow"
}
```

`startArrowhead`/`endArrowhead`: null, "arrow", "bar", "dot", "triangle"

### Binding Arrows to Shapes

To connect arrows to shapes, use bindings:

```json
{
  "startBinding": {
    "elementId": "target-shape-id",
    "focus": 0,
    "gap": 5
  },
  "endBinding": {
    "elementId": "target-shape-id",
    "focus": 0,
    "gap": 5
  }
}
```

When binding, add `boundElements` to the target shape:
```json
{
  "boundElements": [
    { "id": "arrow-id", "type": "arrow" }
  ]
}
```

## Style Values

| Property | Values |
|----------|--------|
| fillStyle | "solid", "hachure", "cross-hatch" |
| strokeStyle | "solid", "dashed", "dotted" |
| roughness | 0 (architect), 1 (artist), 2 (cartoonist) |
| roundness.type | 2 (small radius), 3 (adaptive radius) |

## Color Palette

```
Stroke: #1e1e1e (black), #e03131 (red), #2f9e44 (green), #1971c2 (blue)
Background: transparent, #ffc9c9 (light red), #b2f2bb (light green), 
            #a5d8ff (light blue), #ffec99 (light yellow), #d0bfff (light purple)
```

## Generation Workflow

1. **Plan layout**: Sketch positions on a grid (increments of 50-100px)
2. **Create shapes first**: Generate all boxes/nodes with unique IDs
3. **Add text**: Create text elements, optionally bound to containers
4. **Add connections**: Create arrows with bindings to shape IDs
5. **Update boundElements**: Add arrow references to connected shapes

## ID Generation

Use descriptive IDs: `"memory-client-box"`, `"arrow-client-to-store"`, `"label-extraction"`

## Common Patterns

### Architecture Box with Label

```json
[
  {
    "id": "box-1",
    "type": "rectangle",
    "x": 100, "y": 100,
    "width": 200, "height": 80,
    "strokeColor": "#1e1e1e",
    "backgroundColor": "#a5d8ff",
    "fillStyle": "solid",
    "boundElements": [{ "id": "text-1", "type": "text" }]
  },
  {
    "id": "text-1",
    "type": "text",
    "x": 200, "y": 140,
    "text": "Component",
    "fontSize": 20,
    "textAlign": "center",
    "containerId": "box-1"
  }
]
```

### Connected Flowchart

See `references/examples.md` for complete flowchart and architecture examples.

## Output

Save as `.excalidraw` file (JSON with .excalidraw extension). Present to user for download.

## Replacing ASCII Placeholder Diagrams

When converting asciidoc placeholder diagrams like:
```
+------------------+
|   Component      |
+------------------+
```

Map to proper Excalidraw elements with appropriate sizing, colors, and connections.

## Documentation Diagram Workflow

This project uses a structured workflow for managing documentation diagrams:

### Finding Placeholders

Placeholder diagrams in AsciiDoc files use this pattern:
```asciidoc
.Diagram Title
[cols="1", options="header"]
|===
| [DIAGRAM PLACEHOLDER: Diagram Name]

a|
[source,text]
----
ASCII art representation here
----
|===
```

### Using the Management Script

```bash
# List all placeholder diagrams
make docs-diagrams-list

# Check active image provenance, source bindings and export freshness
make docs-diagrams-status

# Show missing or stale published diagrams and draft sources
make docs-diagrams-missing

# Add image references after generating diagrams
# (review the inserted caption and alt text, and add the manifest entry)
make docs-diagrams-add-refs
```

### File Locations

- **Excalidraw JSON**: `docs/assets/diagrams/excalidraw/{slug}.excalidraw`
- **Exported SVG**: `docs/modules/ROOT/images/diagrams/{slug}.svg` (diagram exports are SVG; only literal UI screenshots stay PNG)
- **Diagram manifest**: `docs/diagrams/manifest.json`
- **Management script**: `scripts/manage_diagrams.py`

### Generating or updating a diagram

1. Read `docs/MAINTAINING.md` and run `make docs-diagrams-status` to inspect active image mappings. A matching filename alone is not provenance.
2. Verify every schema name, operation, threshold and caption against current source. Distinguish application examples, the Python Bolt backend and NAMS.
3. Save editable JSON to `docs/assets/diagrams/excalidraw/{slug}.excalidraw`; preserve IDs and update both ends of bindings.
4. Install optional tools with `npm ci --prefix docs/diagrams` and `npx --prefix docs/diagrams playwright install chromium`.
5. Export a new or changed diagram with `node scripts/export_diagrams.mjs <source.excalidraw> docs/modules/ROOT/images/diagrams/{slug}.svg`, then record it in `docs/diagrams/manifest.json` (step 7). `node scripts/export_diagrams.mjs --all` only re-exports the SVGs the manifest already records, so it never exports a brand-new diagram. Add `--png` to also write a sibling PNG; that PNG then needs its own manifest record. The exporter renders through Excalidraw itself; pages embed the SVG.
6. Inspect the full image and its rendered page at desktop and narrow widths. Check legibility, clipping, arrow endpoints, labels and full alt text. The exporter does not certify semantic correctness.
7. Record source/output SHA-256 values, referring pages, owner and visual review date in `docs/diagrams/manifest.json` only after review. Every export needs a manifest entry in the same change, even before a page embeds it (`"status": "pending-placement"`); `make docs-diagrams-status` fails on an untracked published image. A captured screenshot can have no editable source if its provenance exception is explained.
8. Embed the SVG with a caption line directly above the image macro, then update the relevant page and run `make docs-lint`:

   ```asciidoc
   .Caption sentence that titles the diagram
   image::diagrams/{slug}.svg["Complete alt text that describes the diagram, with commas quoted",width=100%,link=self]
   ```

   The caption is the visible figure title; the alt text describes what the diagram shows and does not repeat the caption.

Do not edit generated OpenWiki diagrams or generated API pages. Preserve historical editable scenes in the canonical directory; the active manifest identifies current published uses. See `docs/diagrams/source-migrations.json` for the migration from former directories.

### Diagram Style Guidelines for This Project

- **Memory types**: Use distinct colors per memory type
  - Short-Term: Green (`#b2f2bb` / `#2f9e44`)
  - Long-Term: Yellow (`#ffec99` / `#f08c00`)
  - Reasoning: Purple (`#d0bfff` / `#9c36b5`)
  - Neo4j/Storage: Blue (`#a5d8ff` / `#1971c2`)

- **Pipeline stages**: Use vertical flow with colored boxes
- **Graph schemas**: Use ellipses for nodes, labeled arrows for relationships
- **Architecture**: Use rectangles with hierarchy (top-level → components → storage)

The official Neo4j Labs UI takes precedence over a generic palette or font suggestion. Use the project policy in `docs/MAINTAINING.md`; do not replace it with a custom product logo.
