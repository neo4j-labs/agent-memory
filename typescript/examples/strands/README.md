# AWS Strands example

Memory-augmented Strands agent using all three integration surfaces:
session persistence, three-tier context injection, and reasoning capture.

## What it shows

- `Neo4jSessionStorage` — agent state automatically persists to a NAMS
  conversation between turns and across script runs.
- `Neo4jConversationManager` — reflections and observations from
  `getContext()` are prepended to every model invocation.
- `registerReasoningHooks` — reasoning steps and tool calls are recorded
  in the graph as the agent thinks. Browse them with
  `client.reasoning.getTraceByConversation(conversationId)`.

All three are wired via the single `connectMemoryToAgent` factory.

## Run it

```bash
cp .env.example .env
# edit .env: set MEMORY_API_KEY and OPENAI_API_KEY
npm install
npm start
```

You'll see a three-turn dialogue. After the run completes, the script
prints the conversation id — re-run with that id as `DEMO_USER_ID` to
see context recall across script runs.

## Memory store (`npm run start:memory-store`)

`Neo4jMemoryStore` implements Strands' `MemoryStore`, so `MemoryManager`
searches the graph before each model call and folds the hits into the turn.
This is the preferred way to give a Strands agent memory.

Recall is **entities-only**: the hosted service exposes entity search but no
preference or fact search endpoint, so those knobs are absent from the options
type rather than silently ignored. `get_entity_graph` traverses one hop.

## Using Amazon Bedrock instead of OpenAI

Strands' first-class pairing is AWS Bedrock. To switch:

```ts
import { BedrockModel } from "@strands-agents/sdk/models/anthropic";

const agent = new Agent({
  model: new BedrockModel({ modelId: "anthropic.claude-3-haiku-20240307-v1:0" }),
  // ...
});
```

Then set `AWS_REGION` and AWS credentials in your environment.

## See also

- [How-to: Strands integration](https://neo4j.com/labs/agent-memory/how-to/typescript/strands)
- [Strands Agents SDK docs](https://strandsagents.com)
