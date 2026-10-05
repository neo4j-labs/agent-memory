import { namsMemory } from "@neo4j-labs/nams-ai-provider/eve";
import { defineMemory } from "eve/memory";

export default defineMemory({
  description: "Durable facts and earlier sessions for the current user, from Neo4j Agent Memory.",
  // In the test this points at the fake server; otherwise at real NAMS.
  provider: namsMemory({ endpoint: process.env.MEMORY_ENDPOINT }),
  scope: "eval-user",
});
