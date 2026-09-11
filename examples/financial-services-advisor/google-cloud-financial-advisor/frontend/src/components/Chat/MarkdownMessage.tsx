import { Box, Code, Heading, Link, Table, Text } from "@chakra-ui/react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Render an assistant reply as Markdown.
 *
 * Gemini, given the compliance-report instructions in the backend's
 * `prompts.py`, reliably answers with headings, bullet lists, bold risk levels
 * and occasionally GFM tables — all of which show up as raw `##`, `**` and `|`
 * when rendered as plain text.
 *
 * Each node maps onto a Chakra component. The mappings take `children` (and
 * `href`) explicitly rather than spreading react-markdown's props, because the
 * DOM-element prop types it passes do not line up with Chakra's polymorphic
 * ones.
 */
const components: Components = {
  p: ({ children }) => (
    <Text mb={2} _last={{ mb: 0 }}>
      {children}
    </Text>
  ),
  h1: ({ children }) => (
    <Heading size="md" mt={3} mb={2}>
      {children}
    </Heading>
  ),
  h2: ({ children }) => (
    <Heading size="sm" mt={3} mb={2}>
      {children}
    </Heading>
  ),
  h3: ({ children }) => (
    <Heading size="xs" mt={3} mb={1}>
      {children}
    </Heading>
  ),
  ul: ({ children }) => (
    <Box as="ul" pl={5} mb={2}>
      {children}
    </Box>
  ),
  ol: ({ children }) => (
    <Box as="ol" pl={5} mb={2}>
      {children}
    </Box>
  ),
  li: ({ children }) => (
    <Box as="li" mb={1}>
      {children}
    </Box>
  ),
  strong: ({ children }) => (
    <Text as="strong" fontWeight="semibold">
      {children}
    </Text>
  ),
  em: ({ children }) => <Text as="em">{children}</Text>,
  a: ({ children, href }) => (
    <Link href={href} target="_blank" rel="noreferrer" colorPalette="brand">
      {children}
    </Link>
  ),
  code: ({ children }) => (
    <Code size="sm" variant="subtle">
      {children}
    </Code>
  ),
  pre: ({ children }) => (
    <Box
      as="pre"
      bg="bg.muted"
      borderRadius="md"
      p={3}
      mb={2}
      overflowX="auto"
      fontFamily="mono"
      fontSize="xs"
    >
      {children}
    </Box>
  ),
  blockquote: ({ children }) => (
    <Box
      borderLeftWidth="3px"
      borderColor="border.emphasized"
      pl={3}
      mb={2}
      color="fg.muted"
    >
      {children}
    </Box>
  ),
  hr: () => <Box borderTopWidth="1px" borderColor="border.subtle" my={3} />,
  table: ({ children }) => (
    <Box overflowX="auto" mb={2}>
      <Table.Root size="sm" variant="outline">
        {children}
      </Table.Root>
    </Box>
  ),
  thead: ({ children }) => <Table.Header>{children}</Table.Header>,
  tbody: ({ children }) => <Table.Body>{children}</Table.Body>,
  tr: ({ children }) => <Table.Row>{children}</Table.Row>,
  th: ({ children }) => <Table.ColumnHeader>{children}</Table.ColumnHeader>,
  td: ({ children }) => <Table.Cell>{children}</Table.Cell>,
};

export function MarkdownMessage({ content }: { content: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </Markdown>
  );
}
