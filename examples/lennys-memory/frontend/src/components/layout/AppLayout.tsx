"use client";

import {
  Box,
  Flex,
  Stack,
  IconButton,
  Text,
  Button,
  HStack,
  Drawer,
  Portal,
  CloseButton,
  Badge,
  Link,
  useBreakpointValue,
} from "@chakra-ui/react";
import {
  LuPanelLeftClose,
  LuPanelLeft,
  LuMenu,
  LuBrain,
  LuExternalLink,
  LuInfo,
} from "react-icons/lu";
import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { Footer } from "./Footer";
import { WelcomeModal } from "@/components/onboarding/WelcomeModal";
import type { QuickStartSuggestion } from "@/lib/types";

interface AppLayoutProps {
  children: React.ReactNode;
  suggestions: QuickStartSuggestion[];
  isLoadingSuggestions: boolean;
  onNewConversation: () => void;
  onSelectSuggestion: (suggestion: QuickStartSuggestion) => void;
  hasActiveConversation: boolean;
}

export function AppLayout({
  children,
  suggestions,
  isLoadingSuggestions,
  onNewConversation,
  onSelectSuggestion,
  hasActiveConversation,
}: AppLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [aboutModalOpen, setAboutModalOpen] = useState(false);

  // Detect mobile viewport - default to false during SSR to avoid hydration mismatch
  const isMobile = useBreakpointValue({ base: true, md: false }) ?? false;

  return (
    <Flex h="100vh" overflow="hidden" bg="bg.canvas">
      {/* Desktop Sidebar */}
      {sidebarOpen && !isMobile && (
        <Box
          w="280px"
          borderRightWidth="1px"
          borderColor="border.subtle"
          bg="bg.panel"
          flexShrink={0}
          hideBelow="md"
        >
          <Sidebar
            suggestions={suggestions}
            isLoading={isLoadingSuggestions}
            onNewConversation={onNewConversation}
            onSelectSuggestion={onSelectSuggestion}
            hasActiveConversation={hasActiveConversation}
          />
        </Box>
      )}

      {/* Mobile Drawer Sidebar */}
      <Drawer.Root
        open={mobileMenuOpen}
        onOpenChange={(e) => setMobileMenuOpen(e.open)}
        placement="start"
      >
        <Portal>
          <Drawer.Backdrop />
          <Drawer.Positioner>
            <Drawer.Content maxW="280px">
              <Drawer.CloseTrigger
                asChild
                position="absolute"
                top="3"
                right="3"
              >
                <CloseButton size="sm" />
              </Drawer.CloseTrigger>
              <Sidebar
                suggestions={suggestions}
                isLoading={isLoadingSuggestions}
                onNewConversation={onNewConversation}
                onSelectSuggestion={onSelectSuggestion}
                hasActiveConversation={hasActiveConversation}
                onSidebarAction={() => setMobileMenuOpen(false)}
              />
            </Drawer.Content>
          </Drawer.Positioner>
        </Portal>
      </Drawer.Root>

      {/* Main content */}
      <Stack flex="1" gap="0" overflow="hidden">
        {/* Header */}
        <Flex
          h="14"
          px={{ base: 2, md: 4 }}
          alignItems="center"
          justifyContent="space-between"
          borderBottomWidth="1px"
          borderColor="border.subtle"
          bg="bg.panel"
        >
          <Flex alignItems="center" gap={2}>
            {/* Mobile hamburger menu */}
            <IconButton
              aria-label="Open menu"
              variant="ghost"
              size="sm"
              hideFrom="md"
              onClick={() => setMobileMenuOpen(true)}
            >
              <LuMenu />
            </IconButton>

            {/* Desktop sidebar toggle */}
            <IconButton
              aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
              variant="ghost"
              size="sm"
              hideBelow="md"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              {sidebarOpen ? <LuPanelLeftClose /> : <LuPanelLeft />}
            </IconButton>

            {/* Logo and title */}
            <Flex alignItems="center" gap={2}>
              <Box color="brand.500">
                <LuBrain size={20} />
              </Box>
              <Text
                fontWeight="semibold"
                color="fg.default"
                fontSize={{ base: "sm", md: "md" }}
                fontFamily="heading"
              >
                <Text as="span" hideBelow="sm">
                  Lenny&apos;s Memory
                </Text>
                <Text as="span" hideFrom="sm">
                  Lenny&apos;s Memory
                </Text>
              </Text>
              <Badge
                size="sm"
                colorPalette="brand"
                variant="subtle"
                hideBelow="sm"
              >
                Beta
              </Badge>
            </Flex>
          </Flex>

          {/* Desktop action buttons */}
          <HStack gap={2} hideBelow="md">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setAboutModalOpen(true)}
            >
              <LuInfo size={16} />
              <Text ml="1.5">About</Text>
            </Button>
            <Link
              href="https://github.com/neo4j-labs/agent-memory"
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button size="sm" variant="ghost" colorPalette="brand">
                <LuExternalLink size={16} />
                <Text ml="1.5" hideBelow="lg">
                  GitHub
                </Text>
              </Button>
            </Link>
          </HStack>

          {/* Mobile action buttons */}
          <HStack gap={1} hideFrom="md">
            <IconButton
              aria-label="About"
              variant="ghost"
              size="sm"
              onClick={() => setAboutModalOpen(true)}
            >
              <LuInfo />
            </IconButton>
            <Link
              href="https://github.com/neo4j-labs/agent-memory"
              target="_blank"
              rel="noopener noreferrer"
            >
              <IconButton aria-label="GitHub" variant="ghost" size="sm">
                <LuExternalLink />
              </IconButton>
            </Link>
          </HStack>
        </Flex>

        {/* Content area */}
        <Box flex="1" overflow="hidden">
          {children}
        </Box>

        {/* Footer with Labs branding */}
        <Footer />
      </Stack>

      {/* About Modal */}
      <WelcomeModal
        isOpen={aboutModalOpen}
        onClose={() => setAboutModalOpen(false)}
      />
    </Flex>
  );
}
