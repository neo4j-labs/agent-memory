import { Link, useLocation } from "react-router";
import {
  Box,
  VStack,
  Text,
  Flex,
  Separator,
  Badge,
  HStack,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import {
  LuLayoutDashboard,
  LuMessageSquare,
  LuUsers,
  LuSearch,
  LuTriangleAlert,
  LuNetwork,
  LuBot,
  LuDatabase,
  LuShield,
} from "react-icons/lu";
import { getAlertSummary } from "../../lib/api";

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  to: string;
  isActive: boolean;
  badge?: number;
}

function NavItem({ icon, label, to, isActive, badge }: NavItemProps) {
  return (
    <Link to={to} style={{ width: "100%" }}>
      <Flex
        align="center"
        px={3}
        py={2.5}
        borderRadius="md"
        bg={isActive ? "brand.subtle" : "transparent"}
        color={isActive ? "brand.fg" : "fg.muted"}
        _hover={{ bg: isActive ? "brand.subtle" : "bg.subtle" }}
        transition="all 0.15s"
        position="relative"
      >
        {isActive && (
          <Box
            position="absolute"
            left={0}
            top="20%"
            bottom="20%"
            w="3px"
            bg="brand.solid"
            borderRadius="full"
          />
        )}
        <Box mr={3}>{icon}</Box>
        <Text
          fontWeight={isActive ? "semibold" : "medium"}
          fontSize="sm"
          flex={1}
        >
          {label}
        </Text>
        {badge !== undefined && badge > 0 && (
          <Badge
            size="sm"
            colorPalette="red"
            variant="solid"
            borderRadius="full"
          >
            {badge}
          </Badge>
        )}
      </Flex>
    </Link>
  );
}

export default function Sidebar() {
  const location = useLocation();

  const { data: alertSummary } = useQuery({
    queryKey: ["alertSummary"],
    queryFn: getAlertSummary,
    refetchInterval: 30000,
  });

  const criticalCount =
    (alertSummary?.critical_unresolved || 0) +
    (alertSummary?.high_unresolved || 0);

  return (
    <Box
      w="250px"
      minH="100vh"
      bg="bg.panel"
      borderRight="1px solid"
      borderColor="border.subtle"
      py={5}
      display="flex"
      flexDirection="column"
    >
      {/* Logo */}
      <Box px={5} mb={6}>
        <HStack gap={2} mb={1}>
          <Box color="brand.fg">
            <LuShield size={20} />
          </Box>
          <Text fontSize="md" fontWeight="bold" color="fg">
            Financial Advisor
          </Text>
        </HStack>
        <Text fontSize="xs" color="fg.muted" ml={7}>
          Google ADK + Neo4j Agent Memory
        </Text>
      </Box>

      {/* Main Navigation */}
      <VStack gap={0.5} px={3} align="stretch">
        <Text
          fontSize="xs"
          fontWeight="semibold"
          color="fg.muted"
          px={3}
          mb={1}
        >
          MAIN
        </Text>
        <NavItem
          icon={<LuLayoutDashboard size={18} />}
          label="Dashboard"
          to="/"
          isActive={
            location.pathname === "/" || location.pathname === "/customers"
          }
        />
        <NavItem
          icon={<LuMessageSquare size={18} />}
          label="Chat"
          to="/chat"
          isActive={location.pathname === "/chat"}
        />
      </VStack>

      <Separator my={3} />

      {/* Compliance Navigation */}
      <VStack gap={0.5} px={3} align="stretch">
        <Text
          fontSize="xs"
          fontWeight="semibold"
          color="fg.muted"
          px={3}
          mb={1}
        >
          COMPLIANCE
        </Text>
        <NavItem
          icon={<LuUsers size={18} />}
          label="Customers"
          to="/customers"
          isActive={location.pathname === "/customers"}
        />
        <NavItem
          icon={<LuSearch size={18} />}
          label="Investigations"
          to="/investigations"
          isActive={location.pathname === "/investigations"}
        />
        <NavItem
          icon={<LuTriangleAlert size={18} />}
          label="Alerts"
          to="/alerts"
          isActive={location.pathname === "/alerts"}
          badge={criticalCount}
        />
        <NavItem
          icon={<LuNetwork size={18} />}
          label="Context Graph"
          to="/graph"
          isActive={location.pathname === "/graph"}
        />
      </VStack>

      {/* Footer */}
      <Box mt="auto" px={5} pt={4}>
        <Separator mb={4} />
        <VStack gap={2} align="stretch">
          <HStack gap={2} color="fg.muted">
            <LuBot size={14} />
            <Text fontSize="xs">5 AI Agents</Text>
          </HStack>
          <HStack gap={2} color="fg.muted">
            <LuDatabase size={14} />
            <Text fontSize="xs">Neo4j Memory</Text>
          </HStack>
        </VStack>
        <Text fontSize="xs" color="fg.subtle" mt={3}>
          v0.1.0
        </Text>
      </Box>
    </Box>
  );
}
