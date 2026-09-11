import { Badge, Box, Flex, Text } from '@chakra-ui/react'
import { motion } from 'motion/react'
import { LuCheck, LuLoader } from 'react-icons/lu'

interface ToolCallCardProps {
  tool: string
  args: Record<string, unknown>
  result?: string
  /** Wall-clock duration reported by the backend's `tool_result` event. */
  durationMs?: number
  /** Chakra colour palette of the owning agent. */
  colorPalette?: string
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value.length > 40 ? `${value.slice(0, 40)}…` : value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value).slice(0, 40)
}

export default function ToolCallCard({
  tool,
  args,
  result,
  durationMs,
  colorPalette = 'gray',
}: ToolCallCardProps) {
  const argEntries = Object.entries(args).slice(0, 3)

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
    >
      <Box
        p={2}
        borderRadius="md"
        borderWidth="1px"
        colorPalette={colorPalette}
        borderColor="colorPalette.emphasized"
        bg="colorPalette.subtle"
        fontSize="xs"
      >
        <Flex align="center" gap={1} mb={1}>
          {result ? (
            <Box color="green.fg">
              <LuCheck size={12} />
            </Box>
          ) : (
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
            >
              <LuLoader size={12} />
            </motion.div>
          )}
          <Text fontFamily="mono" fontWeight="semibold">
            {tool}
          </Text>
          {durationMs !== undefined && (
            <Text color="fg.subtle" fontFamily="mono">
              {durationMs}ms
            </Text>
          )}
        </Flex>

        {argEntries.length > 0 && (
          <Flex gap={1} flexWrap="wrap" mb={result ? 1 : 0}>
            {argEntries.map(([key, value]) => (
              <Badge key={key} size="sm" variant="outline" fontFamily="mono">
                {key}={formatValue(value)}
              </Badge>
            ))}
          </Flex>
        )}

        {result && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}>
            <Text color="green.fg" fontSize="xs" lineClamp={2}>
              {result.slice(0, 120)}
              {result.length > 120 ? '…' : ''}
            </Text>
          </motion.div>
        )}
      </Box>
    </motion.div>
  )
}
