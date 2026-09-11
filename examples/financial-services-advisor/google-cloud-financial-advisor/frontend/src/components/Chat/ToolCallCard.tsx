import { Box, Text, Badge, HStack, Code } from "@chakra-ui/react";
import { motion } from "motion/react";
import { LuWrench, LuCheck, LuLoader } from "react-icons/lu";

interface ToolCallCardProps {
  tool: string;
  args: Record<string, unknown>;
  result?: unknown;
  agent: string;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function ToolCallCard({ tool, args, result }: ToolCallCardProps) {
  const hasResult = result !== undefined;
  const resultStr = formatValue(result);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      <Box
        bg="bg.subtle"
        borderRadius="md"
        px={3}
        py={2}
        border="1px solid"
        borderColor="border.subtle"
      >
        <HStack gap={2} mb={1}>
          <Box color="fg.muted">
            <LuWrench size={12} />
          </Box>
          <Code size="sm" colorPalette="blue" variant="plain">
            {tool}
          </Code>
          {hasResult ? (
            <Badge size="sm" colorPalette="green" variant="subtle">
              <LuCheck size={10} />
            </Badge>
          ) : (
            <Box color="fg.muted">
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
              >
                <LuLoader size={12} />
              </motion.div>
            </Box>
          )}
        </HStack>

        {Object.keys(args).length > 0 && (
          <Text fontSize="xs" color="fg.muted" ml={5}>
            {Object.entries(args)
              .slice(0, 3)
              .map(([k, v]) => `${k}: ${formatValue(v).slice(0, 40)}`)
              .join(", ")}
          </Text>
        )}

        {hasResult && resultStr && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            transition={{ duration: 0.15 }}
          >
            <Text fontSize="xs" color="green.600" ml={5} mt={1}>
              {resultStr.slice(0, 120)}
              {resultStr.length > 120 && "..."}
            </Text>
          </motion.div>
        )}
      </Box>
    </motion.div>
  );
}
