import { Badge, Box, Flex, Icon, Text, VStack } from '@chakra-ui/react'
import {
  FiAlertTriangle,
  FiHome,
  FiMessageSquare,
  FiSearch,
  FiShare2,
  FiUsers,
} from 'react-icons/fi'
import { Link, useLocation } from 'react-router'
import LabsDisclaimer from '../branding/LabsDisclaimer'
import { ColorModeButton } from '../ui/color-mode'

interface NavItemProps {
  icon: React.ElementType
  label: string
  to: string
  isActive?: boolean
}

function NavItem({ icon, label, to, isActive }: NavItemProps) {
  return (
    <Link to={to} style={{ width: '100%' }}>
      <Flex
        align="center"
        p={3}
        borderRadius="md"
        cursor="pointer"
        colorPalette="brand"
        bg={isActive ? 'colorPalette.solid' : 'transparent'}
        color={isActive ? 'colorPalette.contrast' : 'fg.muted'}
        _hover={{ bg: isActive ? 'colorPalette.solid' : 'bg.muted' }}
        transition="all 0.2s"
      >
        <Icon as={icon} boxSize={5} mr={3} />
        <Text fontWeight={isActive ? 'semibold' : 'medium'}>{label}</Text>
      </Flex>
    </Link>
  )
}

// Every entry has a matching route in App.tsx.
const navItems = [
  { icon: FiHome, label: 'Dashboard', to: '/' },
  { icon: FiMessageSquare, label: 'AI Advisor', to: '/chat' },
  { icon: FiUsers, label: 'Customers', to: '/customers' },
  { icon: FiSearch, label: 'Investigations', to: '/investigations' },
  { icon: FiAlertTriangle, label: 'Alerts', to: '/alerts' },
  { icon: FiShare2, label: 'Context Graph', to: '/graph' },
]

export default function Sidebar() {
  const location = useLocation()

  return (
    <Flex
      direction="column"
      w="250px"
      flexShrink={0}
      bg="bg.panel"
      borderRightWidth="1px"
      borderColor="border"
      h="100dvh"
      position="sticky"
      top={0}
    >
      {/* Logo */}
      <Flex align="center" p={4} borderBottomWidth="1px" borderColor="border" gap={3}>
        <Flex
          w={10}
          h={10}
          borderRadius="lg"
          colorPalette="brand"
          bg="colorPalette.solid"
          align="center"
          justify="center"
          flexShrink={0}
        >
          <Text color="colorPalette.contrast" fontWeight="bold" fontSize="lg">
            FS
          </Text>
        </Flex>
        <Box flex="1" minW={0}>
          <Text fontWeight="bold" fontSize="sm" fontFamily="heading" truncate>
            Financial Services
          </Text>
          <Flex align="center" gap={2}>
            <Text fontSize="xs" color="fg.muted">
              Compliance Advisor
            </Text>
            <Badge size="sm" colorPalette="brand">
              Beta
            </Badge>
          </Flex>
        </Box>
      </Flex>

      {/* Navigation */}
      <VStack align="stretch" p={4} gap={1}>
        {navItems.map((item) => (
          <NavItem
            key={item.to}
            icon={item.icon}
            label={item.label}
            to={item.to}
            isActive={location.pathname === item.to}
          />
        ))}
      </VStack>

      {/* Footer */}
      <Box mt="auto" p={4} borderTopWidth="1px" borderColor="border">
        <LabsDisclaimer compact />
        <Flex mt={2} align="center" justify="space-between">
          <Text fontSize="xs" color="fg.subtle">
            Neo4j + AWS Strands
          </Text>
          <ColorModeButton />
        </Flex>
      </Box>
    </Flex>
  )
}
