# Diataxis Documentation Skill

This skill provides guidance for working with the neo4j-agent-memory documentation, which follows the Diataxis framework.

## When to Use This Skill

Use this skill when:
- Adding new documentation pages
- Modifying existing documentation
- Deciding where to place new content
- Reviewing documentation for structure/style

## The Four Quadrants

### 1. Tutorials (`docs/modules/ROOT/pages/tutorials/`)

**Purpose**: Learning-oriented. Help users learn by doing.

**Characteristics**:
- Step-by-step instructions
- Practical, hands-on lessons
- Focus on learning, not accomplishing
- Clear outcomes at each step
- Minimal explanation (link to Explanation for details)

**Writing Style**:
- Use "we will..." (inclusive)
- Provide exact commands/code to run
- Show expected output at each step
- Don't offer choices - provide one clear path

**Example titles**:
- "Build your first memory agent"
- "Create a knowledge graph from documents"

### 2. How-To Guides (`docs/modules/ROOT/pages/how-to/`)

**Purpose**: Task-oriented. Help users accomplish specific goals.

**Characteristics**:
- Problem-focused
- Assumes user knows what they want
- Practical steps to solve a problem
- No teaching, just doing

**Writing Style**:
- Start with the goal: "To do X, you need to..."
- Numbered steps for the solution
- Include prerequisites if needed
- End with verification/testing

**Example titles**:
- "Configure entity extraction"
- "Enable location geocoding"
- "Use with PydanticAI"

### 3. Reference (`docs/modules/ROOT/pages/reference/`)

**Purpose**: Information-oriented. Provide facts for lookup.

**Characteristics**:
- Comprehensive and accurate
- Structure mirrors the code/product
- Austere, neutral tone
- Designed for scanning, not reading

**Writing Style**:
- Use tables for parameters/options
- Document ALL options, not just common ones
- Include types, defaults, constraints
- Keep descriptions factual and brief

**Example titles**:
- "Configuration Reference"
- "CLI Command Reference"
- "API Reference"

### 4. Explanation (`docs/modules/ROOT/pages/explanation/`)

**Purpose**: Understanding-oriented. Help users understand concepts.

**Characteristics**:
- Discusses "why" not "how"
- Provides context and background
- Can include opinions and alternatives
- Discursive, can be read reflectively

**Writing Style**:
- Explain design decisions
- Discuss alternatives considered
- Connect to broader concepts
- Use diagrams for complex concepts

**Example titles**:
- "Understanding the Three Memory Types"
- "The POLE+O Data Model Explained"
- "How Entity Extraction Works"

## Decision Tree: Where Does Content Belong?

```
Is it about DOING something?
├── YES: Is it for LEARNING or ACCOMPLISHING?
│   ├── LEARNING → Tutorial
│   └── ACCOMPLISHING → How-To Guide
└── NO: Is it FACTS or UNDERSTANDING?
    ├── FACTS → Reference
    └── UNDERSTANDING → Explanation
```

## Common Mistakes to Avoid

1. **Don't mix quadrants**: A how-to guide shouldn't teach concepts (link to explanation instead)

2. **Don't put reference in tutorials**: Link to reference for complete option lists

3. **Don't explain in reference**: Keep reference factual; explanation goes elsewhere

4. **Don't make tutorials choose**: Provide one path, not options

## File Naming Conventions

- Use lowercase with hyphens: `entity-extraction.adoc`
- Use descriptive names: `first-agent-memory.adoc` not `tutorial-1.adoc`
- Integration guides: `how-to/integrations/{framework}.adoc`
- API reference: `reference/api/{component}.adoc`

## Cross-Referencing

Use `xref:` for internal links:
```asciidoc
See xref:explanation/memory-types.adoc[Understanding Memory Types] for conceptual background.

For configuration details, see xref:reference/configuration.adoc#extraction[Extraction Configuration].
```

## Adding New Content

1. Determine the quadrant using the decision tree above
2. Create the file in the appropriate directory
3. Add to the index page for that quadrant
4. Add cross-references from related pages
5. Add the page to `docs/modules/ROOT/nav.adoc` and preserve useful old section IDs.
6. Run `make docs-lint` to build once and check rendered links, fragments, images and quadrant coverage.
7. Follow `docs/MAINTAINING.md` for executable examples, backend scope and release verification.

## Diagrams and screenshots

Prefer a completed image with an editable source in `docs/assets/diagrams/excalidraw/` and an entry in `docs/diagrams/manifest.json`. Publish exports under `docs/modules/ROOT/images/diagrams/`. Quote alt text containing commas and verify its rendered dimensions. Use placeholders only while drafting; they are not a completed visual.

### Draft placeholders

For diagrams to be added later:
```asciidoc
.Architecture Overview
[cols="1", options="header"]
|===
| [DIAGRAM PLACEHOLDER: Memory Architecture]

a|
[source,text]
----
+------------------+
| MemoryClient     |
+------------------+
| Short | Long | R |
+------------------+
----
|===
```

For screenshots:
```asciidoc
[NOTE]
====
**[SCREENSHOT PLACEHOLDER]**

_Description: Neo4j Browser showing entity nodes and relationships_

Image to add: `images/screenshots/entity-graph.png`
====
```

## Building and Testing

```bash
cd docs
npm run build      # Build all pages
npm run serve      # Preview the existing build; rebuild after edits
npm run lint       # Fresh build plus source/rendered checks
```
