import { Box, Code, Heading, Link, Stack, Text } from "@chakra-ui/react";

/**
 * Shown instead of the app when the server could not reach NAMS — almost always
 * a missing or wrong `MEMORY_API_KEY`. Keeping this as a rendered page rather
 * than a thrown error means `npm run dev` on a fresh clone explains itself.
 */
export function SetupNotice({ message }: { message: string }) {
  return (
    <Box maxW="3xl" mx="auto" p={10}>
      <Stack gap={4}>
        <Heading size="lg">Memory service not reachable</Heading>
        <Text color="fg.muted">
          The app could not create a conversation in the Neo4j Agent Memory Service.
        </Text>
        <Code p={3} borderRadius="md" whiteSpace="pre-wrap" display="block">
          {message}
        </Code>
        <Text>
          Copy <Code>.env.example</Code> to <Code>.env.local</Code> and set{" "}
          <Code>MEMORY_API_KEY</Code> (and <Code>OPENAI_API_KEY</Code> for the model). Keys come
          from{" "}
          <Link href="https://memory.neo4jlabs.com" target="_blank" rel="noreferrer">
            memory.neo4jlabs.com
          </Link>
          .
        </Text>
      </Stack>
    </Box>
  );
}
