"use client";

import { useCallback, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  HStack,
  Heading,
  Input,
  SimpleGrid,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";
import {
  addPreference,
  getPreferences,
  type MemoryPreference,
} from "@/lib/api";
import { useAsyncData } from "@/lib/useAsyncData";

interface PreferencePanelProps {
  sessionId: string;
  userId: string;
}

const categoryColors: Record<string, string> = {
  brand: "purple",
  category: "blue",
  style: "green",
  price: "orange",
  budget: "orange",
  size: "pink",
  color: "cyan",
};

function paletteFor(category: string): string {
  return categoryColors[category.toLowerCase()] ?? "gray";
}

export function PreferencePanel({ sessionId, userId }: PreferencePanelProps) {
  const load = useCallback(() => getPreferences(sessionId), [sessionId]);
  const {
    data,
    error,
    isLoading,
    reload: loadPreferences,
  } = useAsyncData(`preferences|${sessionId}`, load);
  const preferences: MemoryPreference[] = data?.preferences ?? [];

  // Group preferences by category
  const groupedPreferences = preferences.reduce<
    Record<string, MemoryPreference[]>
  >((acc, pref) => {
    const bucket = acc[pref.category] ?? [];
    bucket.push(pref);
    acc[pref.category] = bucket;
    return acc;
  }, {});

  return (
    <Box>
      <HStack justify="space-between" mb={2} flexWrap="wrap" gap={3}>
        <Heading size="lg">All learned preferences (global)</Heading>
        <Button
          size="sm"
          variant="outline"
          onClick={loadPreferences}
          loading={isLoading}
        >
          Refresh
        </Button>
      </HStack>
      <Text color="fg.muted" fontSize="sm" mb={6}>
        This backend keeps one shared preference store: reads go through
        <Text as="span" fontFamily="mono">
          {" "}
          search_preferences{" "}
        </Text>
        without a tenant filter, so every shopper sees the same list. Switching
        shoppers in the header changes the conversation and the memory graph,
        not this panel.
      </Text>

      <AddPreferenceForm
        sessionId={sessionId}
        userId={userId}
        onAdded={loadPreferences}
      />

      {isLoading ? (
        <Box textAlign="center" py={10}>
          <Spinner size="lg" color="teal.solid" />
          <Text mt={4} color="fg.muted">
            Loading preferences...
          </Text>
        </Box>
      ) : error ? (
        <Card.Root bg="bg.error" borderColor="border.error">
          <Card.Body>
            <Text color="fg.error">{error}</Text>
          </Card.Body>
        </Card.Root>
      ) : preferences.length === 0 ? (
        <Card.Root>
          <Card.Body textAlign="center" py={10}>
            <Text color="fg.muted" mb={4}>
              No preferences learned yet.
            </Text>
            <Text color="fg.subtle" fontSize="sm">
              Start chatting with the assistant and express your preferences.
              For example, say &ldquo;I prefer Nike brand&rdquo; or &ldquo;My
              budget is under $150&rdquo; — or add one directly with the form
              above.
            </Text>
          </Card.Body>
        </Card.Root>
      ) : (
        <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} gap={6}>
          {Object.entries(groupedPreferences).map(([category, prefs]) => (
            <Card.Root key={category}>
              <Card.Header>
                <HStack>
                  <Badge colorPalette={paletteFor(category)} size="lg">
                    {category}
                  </Badge>
                  <Text color="fg.muted" fontSize="sm">
                    ({prefs.length})
                  </Text>
                </HStack>
              </Card.Header>
              <Card.Body>
                <VStack align="stretch" gap={3}>
                  {prefs.map((pref) => (
                    <Box
                      key={pref.id}
                      p={3}
                      bg="bg.subtle"
                      borderRadius="md"
                      borderLeftWidth="3px"
                      borderLeftColor={`${paletteFor(category)}.solid`}
                    >
                      <Text fontWeight="medium">{pref.preference}</Text>
                      {pref.context && (
                        <Text fontSize="sm" color="fg.muted" mt={1}>
                          Context: {pref.context}
                        </Text>
                      )}
                      {pref.confidence !== undefined && (
                        <HStack mt={2}>
                          <Text fontSize="xs" color="fg.subtle">
                            Confidence:
                          </Text>
                          <Box
                            flex={1}
                            h="4px"
                            bg="bg.muted"
                            borderRadius="full"
                          >
                            <Box
                              h="100%"
                              w={`${pref.confidence * 100}%`}
                              bg={`${paletteFor(category)}.solid`}
                              borderRadius="full"
                            />
                          </Box>
                          <Text fontSize="xs" color="fg.subtle">
                            {Math.round(pref.confidence * 100)}%
                          </Text>
                        </HStack>
                      )}
                    </Box>
                  ))}
                </VStack>
              </Card.Body>
            </Card.Root>
          ))}
        </SimpleGrid>
      )}

      {/* Explanation */}
      <Card.Root mt={6} bg="bg.subtle" borderColor="teal.muted">
        <Card.Body>
          <Heading size="sm" mb={2}>
            How preferences work
          </Heading>
          <Text color="fg.muted" fontSize="sm">
            The agent writes preferences itself through the{" "}
            <Text as="span" fontFamily="mono">
              remember_preference
            </Text>{" "}
            tool that the library&rsquo;s Microsoft Agent Framework integration
            provides, and the context provider injects matching ones into the
            next prompt. The form above is the deterministic path to the same
            store (<Text as="span" fontFamily="mono">POST /memory/preferences</Text>
            ), which is handy for demoing recall without spending an LLM call.
          </Text>
        </Card.Body>
      </Card.Root>
    </Box>
  );
}

/** Writes straight to long-term memory: the UI's only write path. */
function AddPreferenceForm({
  sessionId,
  userId,
  onAdded,
}: {
  sessionId: string;
  userId: string;
  onAdded: () => void;
}) {
  const [category, setCategory] = useState("brand");
  const [preference, setPreference] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!preference.trim() || isSaving) return;
    setIsSaving(true);
    setStatus(null);
    try {
      await addPreference({
        category: category.trim() || "general",
        preference: preference.trim(),
        sessionId,
        userId,
      });
      setPreference("");
      setStatus("Saved to long-term memory.");
      onAdded();
    } catch (err) {
      setStatus(
        err instanceof Error ? err.message : "Could not save the preference"
      );
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card.Root mb={6} bg="bg.panel">
      <Card.Body>
        <Box as="form" onSubmit={handleSubmit}>
          <HStack gap={2} flexWrap="wrap">
            <Input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="Category"
              size="sm"
              maxW="160px"
              aria-label="Preference category"
            />
            <Input
              value={preference}
              onChange={(e) => setPreference(e.target.value)}
              placeholder='Preference, e.g. "Nike"'
              size="sm"
              flex={1}
              minW="200px"
              aria-label="Preference"
            />
            <Button
              type="submit"
              size="sm"
              colorPalette="teal"
              loading={isSaving}
              disabled={!preference.trim()}
            >
              Remember this
            </Button>
          </HStack>
        </Box>
        {status && (
          <Text mt={2} fontSize="sm" color="fg.muted">
            {status}
          </Text>
        )}
      </Card.Body>
    </Card.Root>
  );
}
