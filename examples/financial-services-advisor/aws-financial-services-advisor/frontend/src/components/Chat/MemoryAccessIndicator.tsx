import { Badge, Flex, Text } from '@chakra-ui/react'
import { motion } from 'motion/react'
import { LuDatabase, LuSave, LuSearch } from 'react-icons/lu'

interface MemoryAccessIndicatorProps {
  operation: string
  tool: string
  query?: string
}

/** One read from or write to agent memory, as reported by the backend. */
export default function MemoryAccessIndicator({
  operation,
  tool,
  query,
}: MemoryAccessIndicatorProps) {
  const isSearch = operation === 'search'
  const palette = isSearch ? 'brand' : 'green'
  const Icon = isSearch ? LuSearch : LuSave

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <Flex
        align="center"
        gap={1.5}
        p={1.5}
        borderRadius="md"
        borderWidth="1px"
        colorPalette={palette}
        borderColor="colorPalette.emphasized"
        bg="colorPalette.subtle"
        fontSize="xs"
      >
        <LuDatabase size={12} />
        <Icon size={10} />
        <Badge size="sm" colorPalette={palette}>
          {operation}
        </Badge>
        <Text fontFamily="mono" color="colorPalette.fg">
          {tool}
        </Text>
        {query && (
          <Text color="fg.muted" truncate maxW="150px">
            {query}
          </Text>
        )}
      </Flex>
    </motion.div>
  )
}
