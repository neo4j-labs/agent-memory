# Diagram Descriptions for neo4j-agent-memory Docs

Generated diagrams for Antora documentation. All PNGs go to
`docs/modules/ROOT/images/diagrams/`. Excalidraw sources go to
`docs/assets/images/diagrams/excalidraw/`.

---

## 1. poleo-model.png
**Page**: `explanation/poleo-model.adoc`
**Replaces**: ASCII art table showing 5 entity types

### Layout
5 colored boxes in a 3-2 grid, each showing entity type name + key subtypes.

| Box | Color | Subtypes to show |
|-----|-------|-----------------|
| PERSON | Light green (`#b2f2bb`) | Individual, Professional, Alias |
| OBJECT | Light orange (`#ffd8a8`) | Vehicle, Device, Document, Weapon |
| LOCATION | Light blue (`#a5d8ff`) | Address, Region, Landmark, Country |
| EVENT | Light yellow (`#fff3bf`) | Meeting, Transaction, Incident |
| ORGANIZATION | Light purple (`#d0bfff`) | Company, Government, NGO |

Title at top: "POLE+O Entity Model". Camera XL (1200x900).

---

## 2. message-chain.png
**Page**: `how-to/messages.adoc`
**Describes**: How messages are stored and linked in short-term memory

### Layout
Vertical flow:
```
[Conversation] 
  --FIRST_MESSAGE-->
[Message: user "Hello"]
  --NEXT_MESSAGE-->
[Message: assistant "Hi there"]
  --NEXT_MESSAGE-->
[Message: user "..."]
```
On the right side, branch arrows from messages:
```
[Message] --MENTIONS--> [Entity: Person]
[Message] --MENTIONS--> [Entity: Org]
```
Color: Conversation=light teal, Messages=light blue (user) / light green (assistant), Entities=light purple.

---

## 3. multi-tenant-scoping.png
**Page**: `how-to/multi-tenancy.adoc`
**Describes**: How User nodes scope data per tenant in a shared Neo4j instance

### Layout
Two parallel columns:
```
[:User sara@]          [:User liam@]
     |                      |
HAS_CONVERSATION      HAS_CONVERSATION
     |                      |
[Conv: sara-2026]     [Conv: liam-2026]
     |                      |
  [Messages]            [Messages]

HAS_PREFERENCE        HAS_PREFERENCE
     |                      |
[Pref: healthcare]    [Pref: fintech]
```
Underneath both: shared Neo4j cylinder / box.
Colors: Users=light purple, Sara side=light blue, Liam side=light orange, Neo4j=light teal.

---

## 4. buffered-write-flow.png
**Page**: `how-to/buffered-writes.adoc`
**Describes**: Fire-and-forget buffered write architecture

### Layout
Left-to-right flow with two horizontal swimlanes:
```
Top lane (Agent response path):
[Agent Turn] --submit()--> [Buffer Queue] 
    |                    (max_pending=200)
[Returns immediately]

Bottom lane (Background drain path):
                 [Buffer Queue] --drain--> [Neo4j]
                                         (async)
```
Arrow from "Returns immediately" back to user showing agent is not blocked.
Annotation: "Back-pressure at max_pending" on the queue.
Colors: Agent=light purple, Buffer=light yellow, Neo4j=light teal.

---

## 5. entity-dedup-flow.png
**Page**: `how-to/deduplication.adoc`
**Describes**: How entity deduplication works with similarity thresholds

### Layout
Vertical decision flowchart:
```
[New Entity] 
     |
[Compute Similarity]
(embedding + fuzzy)
     |
  <similarity >= 0.95?>
   YES /          \ NO
      /            \
[Auto-Merge]    <similarity >= 0.85?>
(aliases kept)    YES /       \ NO
                     /         \
              [Flag SAME_AS]  [Create New]
              (status=pending) (no duplicate)
```
Colors: Input=light blue, decision diamonds=light yellow, 
Auto-merge=light green, Flag=light orange, Create=light teal.

---

## 6. reasoning-trace-graph.png
**Page**: `how-to/reasoning-traces.adoc`
**Replaces**: ASCII art trace structure diagram

### Layout
Graph structure showing node types and relationships:
```
[Message] --INITIATED_BY--> [ReasoningTrace "Product search"]
                                    |
                              HAS_STEP
                                    |
                    ┌───────────────┼───────────────┐
                    |               |               |
               [Step 1]        [Step 2]        [Step 3]
              "Search"        "Filter"       "Recommend"
                    |               |               |
              USES_TOOL        USES_TOOL       USES_TOOL
                    |               |               |
            [ToolCall:       [ToolCall:     [ToolCall:
            search_api]      get_prefs]    rank_items]
                    |
                TOUCHED
                    |
             [Entity: Nike]
```
Colors: Message=light blue, Trace=light purple, Steps=light purple (lighter),
ToolCalls=light orange, Entities=light green.

---

## 7. mcp-server-architecture.png
**Page**: `reference/mcp-tools.adoc` / `tutorials/mcp-server.adoc`
**Describes**: How the MCP server connects Claude to Neo4j memory

### Layout
Top-to-bottom flow with layers:
```
[Claude Desktop / Claude Code / Any MCP Client]
              |
     MCP Protocol (stdio | SSE | HTTP)
              |
     [MCP Server]
       |           |
  [Core Profile]  [Extended Profile]
  6 tools         16 tools
  2 resources     4 resources
  1 prompt        3 prompts
              |
      [MemoryClient]
       |       |       |
     [STM]   [LTM]   [RTM]
     Short   Long   Reasoning
              |
          [Neo4j]
```
Colors: Client=light blue, Server=light purple, Profiles=light yellow,
MemoryClient=light teal, Neo4j=light teal (darker).
