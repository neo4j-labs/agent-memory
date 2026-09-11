"use client";

import { Flex, Link, Text } from "@chakra-ui/react";

const REPO_URL = "https://github.com/neo4j-labs/neo4j-agent-memory";
const COMMUNITY_URL = "https://community.neo4j.com/";
const DOCS_URL = "https://neo4j-agent-memory.vercel.app/";

/**
 * Compact Neo4j Labs footer with the Labs disclaimer and the links every
 * Labs example is expected to carry (repository, docs, community forum).
 */
export function LabsFooter() {
  return (
    <Flex
      as="footer"
      px="4"
      py="2"
      gap="3"
      alignItems="center"
      justifyContent="space-between"
      flexWrap="wrap"
      borderTopWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
      fontSize="xs"
      color="fg.muted"
    >
      <Text>
        A Neo4j Labs example — community-supported, not a Neo4j product.
      </Text>
      <Flex gap="4">
        <Link
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
          colorPalette="brand"
        >
          GitHub
        </Link>
        <Link
          href={DOCS_URL}
          target="_blank"
          rel="noreferrer"
          colorPalette="brand"
        >
          Docs
        </Link>
        <Link
          href={COMMUNITY_URL}
          target="_blank"
          rel="noreferrer"
          colorPalette="brand"
        >
          Community forum
        </Link>
      </Flex>
    </Flex>
  );
}
