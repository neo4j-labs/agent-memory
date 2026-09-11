/**
 * Seed a demo conversation so the rail is populated on first load.
 *
 * An empty canvas is the worst possible first impression for a graph-memory
 * demo, so this script writes a short prior trip-planning conversation, waits
 * for NAMS to extract its entities, and prints the `/c/<id>` URL to open.
 *
 *   npm run seed
 *   npm run dev     # then open the printed URL
 *
 * It only needs `MEMORY_API_KEY` — extraction and embeddings run server-side, so
 * no model key is involved.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";

const USER_ID = process.env.DEMO_USER_ID ?? "traveller@example.com";

/** A prior planning conversation — the constraints are the interesting part. */
const TRANSCRIPT: Array<{ role: "user" | "assistant"; content: string }> = [
  {
    role: "user",
    content:
      "I'm planning two weeks in Japan in April. My budget is about £3,000 including flights.",
  },
  {
    role: "assistant",
    content:
      "April is cherry blossom season, so book accommodation early. £3,000 works if you base yourself in two or three cities and use a rail pass rather than domestic flights.",
  },
  {
    role: "user",
    content: "I'm vegetarian, and long bus rides make me ill — I'd rather take trains.",
  },
  {
    role: "assistant",
    content:
      "Noted: vegetarian and trains only. Tokyo, Kyoto and Kanazawa are all well served by the Shinkansen and all have good vegetarian options.",
  },
  { role: "user", content: "Is Kanazawa worth it over Osaka?" },
  {
    role: "assistant",
    content:
      "Kanazawa is quieter and has Kenrokuen garden and the 21st Century Museum of Contemporary Art. Osaka is louder and better for food. With your train preference both are easy from Kyoto.",
  },
  { role: "user", content: "Let's rule out Osaka then. Too busy for me." },
  {
    role: "assistant",
    content:
      "Osaka is off the list. That leaves Tokyo, Kyoto and Kanazawa, which fits two weeks comfortably.",
  },
  { role: "user", content: "Which rail pass should I get?" },
  {
    role: "assistant",
    content:
      "A 14-day Japan Rail Pass from Japan Railways covers the Shinkansen legs between all three cities and pays for itself on the Tokyo to Kyoto run alone.",
  },
  { role: "user", content: "Any vegetarian restaurant you'd book in advance in Kyoto?" },
  {
    role: "assistant",
    content:
      "Shigetsu inside Tenryu-ji serves shojin ryori, the Buddhist vegetarian cuisine, and takes reservations. Book it for a lunch slot.",
  },
  { role: "user", content: "I'd like one day trip from Tokyo that isn't Mount Fuji." },
  {
    role: "assistant",
    content:
      "Nikko — the Toshogu shrine complex, reachable on the Tobu line in under two hours, no buses required.",
  },
  {
    role: "user",
    content: "Good. Hold all of that; I'll come back to finalise the Kanazawa hotel.",
  },
];

/**
 * Entities written explicitly so the graph is never empty, even before the
 * background extractor has run. NAMS resolves duplicates on create, so running
 * the script twice does not double them up.
 */
const ENTITIES: Array<{ name: string; type: string; description: string }> = [
  { name: "Tokyo", type: "LOCATION", description: "First base of the April trip" },
  { name: "Kyoto", type: "LOCATION", description: "Second base; temple and garden days" },
  { name: "Kanazawa", type: "LOCATION", description: "Third base; quieter alternative to Osaka" },
  { name: "Osaka", type: "LOCATION", description: "Ruled out by the traveller as too busy" },
  { name: "Nikko", type: "LOCATION", description: "Day trip from Tokyo, reachable by train" },
  { name: "Japan Railways", type: "ORGANIZATION", description: "Issuer of the 14-day rail pass" },
  { name: "Shigetsu", type: "ORGANIZATION", description: "Vegetarian shojin ryori restaurant in Kyoto" },
  { name: "Japan Rail Pass", type: "OBJECT", description: "14-day pass covering the Shinkansen legs" },
];

async function main(): Promise<void> {
  if (!process.env.MEMORY_API_KEY) {
    throw new Error("Set MEMORY_API_KEY — see .env.example");
  }

  const client = new MemoryClient({
    apiKey: process.env.MEMORY_API_KEY,
    workspaceId: process.env.MEMORY_WORKSPACE_ID,
    ...(process.env.MEMORY_ENDPOINT ? { endpoint: process.env.MEMORY_ENDPOINT } : {}),
  });

  try {
    const conversation = await client.shortTerm.createConversation({
      userId: USER_ID,
      metadata: { source: "nextjs-memory-chat", seeded: true },
    });
    console.log(`Created conversation ${conversation.id} for ${USER_ID}`);

    // One request for the whole transcript instead of 15 round-trips.
    const written = await client.shortTerm.bulkAddMessages(conversation.id, TRANSCRIPT);
    console.log(`Wrote ${written.length} messages`);

    for (const entity of ENTITIES) {
      const created = await client.longTerm.addEntity(entity.name, entity.type, {
        description: entity.description,
      });
      console.log(`  ${created.name} (${created.type})`);
    }

    // Background extraction will add more entities from the transcript itself.
    const settled = await client.longTerm.waitForExtraction({
      query: "Japan trip",
      expectedNames: ["Kyoto"],
      timeoutMs: 30_000,
    });
    console.log(
      settled
        ? "Extraction caught up."
        : "Extraction still running — the rail's badge will show it settle.",
    );

    const graph = await client.longTerm.getEntityGraph();
    console.log(`Graph now holds ${graph.nodes.length} node(s), ${graph.edges.length} edge(s)`);
    console.log(`\nOpen http://localhost:3000/c/${conversation.id}`);
  } finally {
    await client.close();
  }
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
