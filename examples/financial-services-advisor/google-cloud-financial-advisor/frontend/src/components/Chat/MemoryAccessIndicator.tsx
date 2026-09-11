import { HStack, Text, Badge } from '@chakra-ui/react'
import { motion } from 'motion/react'
import { LuDatabase, LuSearch, LuSave } from 'react-icons/lu'

interface MemoryAccessIndicatorProps {
  operation: 'search' | 'store'
  tool: string
  query?: string
}

export function MemoryAccessIndicator({ operation, tool, query }: MemoryAccessIndicatorProps) {
  const isSearch = operation === 'search'

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <HStack
        gap={2}
        px={3}
        py={1.5}
        bg={isSearch ? 'blue.50' : 'green.50'}
        borderRadius="md"
        border="1px solid"
        borderColor={isSearch ? 'blue.100' : 'green.100'}
      >
        <motion.div
          animate={{ scale: [1, 1.2, 1] }}
          transition={{ duration: 0.6, ease: 'easeInOut' }}
        >
          <HStack gap={1} color={isSearch ? 'blue.500' : 'green.500'}>
            <LuDatabase size={12} />
            {isSearch ? <LuSearch size={10} /> : <LuSave size={10} />}
          </HStack>
        </motion.div>
        <Badge size="sm" variant="subtle" colorPalette={isSearch ? 'blue' : 'green'}>
          Neo4j {isSearch ? 'Search' : 'Store'}
        </Badge>
        <Text fontSize="xs" color="fg.muted" truncate maxW="200px">
          {tool}
          {query && `: "${query}"`}
        </Text>
      </HStack>
    </motion.div>
  )
}
