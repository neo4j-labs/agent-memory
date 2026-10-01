"use client";

import {
  Badge,
  Box,
  Button,
  Code,
  Flex,
  IconButton,
  List,
  NativeSelect,
  Spinner,
  Stack,
  Table,
  Text,
} from "@chakra-ui/react";
import { useCallback, useMemo, useState } from "react";
import {
  LuBookOpen,
  LuGitCompare,
  LuHistory,
  LuRefreshCw,
  LuReplace,
  LuTags,
} from "react-icons/lu";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyRow, LoadingRow, PanelSection } from "@/components/ui/PanelSection";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { useApi } from "@/hooks/useApi";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, shortId } from "@/lib/format";
import { fullType, labelColor } from "@/lib/labels";
import type {
  ActivateResponse,
  OntologyOverview,
  OntologyRevision,
  RenameResponse,
} from "@/lib/types";
import { DiffView } from "./DiffView";

/** The demo's one revision step (the same one ontology-lifecycle-bolt runs). */
const RENAME = { old: "Ticket", new: "SupportCase", validation_mode: "strict" };

interface OntologyPanelProps {
  refreshKey: number;
  /** Called after activate/rename so Memory, Graph and health refetch. */
  onOntologyChanged: () => void;
}

function ModeBadge({ mode }: { mode: string | null | undefined }) {
  if (!mode) return <Badge size="sm">no mode</Badge>;
  return (
    <Badge size="sm" colorPalette={mode === "strict" ? "orange" : "green"}>
      {mode}
    </Badge>
  );
}

function ActiveSummary({ overview }: { overview: OntologyOverview }) {
  const { active, client } = overview;
  const clientDiffers =
    !!active &&
    (client.domain_id !== active.domain_id ||
      client.validation_mode !== active.validation_mode);

  return (
    <Stack gap="2">
      {active ? (
        <Box
          p="3"
          borderRadius="md"
          borderWidth="1px"
          borderColor="border.subtle"
          bg="bg.subtle"
        >
          <Flex alignItems="center" gap="2" flexWrap="wrap">
            <Text fontWeight="semibold">{active.domain_id}</Text>
            <Badge colorPalette="brand" size="sm">
              revision {active.revision}
            </Badge>
            <ModeBadge mode={active.validation_mode} />
            <Badge colorPalette="green" variant="outline" size="sm">
              active
            </Badge>
          </Flex>
          <Text fontSize="xs" color="fg.muted" mt="1" fontFamily="mono">
            version {shortId(active.version_id, 12)} · ontology{" "}
            {shortId(active.ontology_id, 12)}
          </Text>
        </Box>
      ) : (
        <StatusAlert
          status="info"
          title="No ontology is active"
          description="Run `make seed` to import the support-desk ontology and activate revision 1."
        />
      )}

      <Flex
        alignItems="center"
        gap="2"
        fontSize="xs"
        color="fg.muted"
        flexWrap="wrap"
      >
        <Text>The app’s client resolved:</Text>
        <Code size="sm">{client.domain_id ?? "(none)"}</Code>
        <ModeBadge mode={client.validation_mode} />
        {!clientDiffers && active ? (
          <Badge size="xs" colorPalette="green" variant="subtle">
            matches the active revision
          </Badge>
        ) : null}
      </Flex>
      {clientDiffers ? (
        <StatusAlert
          status="warning"
          title="The app's client is on a different ontology"
          description={`A bolt client resolves its ontology when it connects. The database has ${active?.domain_id} (${active?.validation_mode}) active, but the backend's client resolved ${client.domain_id ?? "nothing"} (${client.validation_mode ?? "no mode"}). Activating through this panel reconnects the client.`}
        />
      ) : null}
    </Stack>
  );
}

function RevisionsTable({
  revisions,
  busyVersionId,
  onActivate,
}: {
  revisions: OntologyRevision[];
  busyVersionId: string | null;
  onActivate: (revision: OntologyRevision) => void;
}) {
  const sorted = [...revisions].sort((a, b) => b.revision - a.revision);
  return (
    <Table.ScrollArea borderWidth="1px" borderRadius="md">
      <Table.Root size="sm">
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>Rev</Table.ColumnHeader>
            <Table.ColumnHeader>Mode</Table.ColumnHeader>
            <Table.ColumnHeader>Labels</Table.ColumnHeader>
            <Table.ColumnHeader>Created</Table.ColumnHeader>
            <Table.ColumnHeader textAlign="end">
              <Text srOnly>Action</Text>
            </Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {sorted.map((revision) => (
            <Table.Row key={revision.version_id}>
              <Table.Cell fontWeight="medium">{revision.revision}</Table.Cell>
              <Table.Cell>
                <ModeBadge mode={revision.validation_mode} />
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs">{revision.labels.join(", ")}</Text>
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs" whiteSpace="nowrap">
                  {formatDateTime(revision.created_at)}
                </Text>
              </Table.Cell>
              <Table.Cell textAlign="end">
                {revision.is_active ? (
                  <Badge colorPalette="green" size="sm">
                    active
                  </Badge>
                ) : (
                  <Button
                    size="2xs"
                    variant="outline"
                    loading={busyVersionId === revision.version_id}
                    disabled={busyVersionId !== null}
                    onClick={() => onActivate(revision)}
                  >
                    Activate
                  </Button>
                )}
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Table.ScrollArea>
  );
}

function DiffExplorer({ revisions }: { revisions: OntologyRevision[] }) {
  const numbers = useMemo(
    () => [...new Set(revisions.map((r) => r.revision))].sort((a, b) => a - b),
    [revisions],
  );
  // Default to the two newest revisions, oldest first.
  const defaultTo = numbers[numbers.length - 1] ?? null;
  const defaultFrom = numbers.length > 1 ? numbers[numbers.length - 2] : null;

  const [picked, setPicked] = useState<{ from: number | null; to: number | null }>(
    { from: null, to: null },
  );
  const from = picked.from !== null && numbers.includes(picked.from) ? picked.from : defaultFrom;
  const to = picked.to !== null && numbers.includes(picked.to) ? picked.to : defaultTo;

  const load = useMemo(
    () =>
      from !== null && to !== null && from !== to
        ? (signal: AbortSignal) => api.ontology.diff(from, to, { signal })
        : null,
    [from, to],
  );
  const { data, error, loading } = useApi(load);

  if (numbers.length < 2) {
    return (
      <EmptyRow>
        Only one revision so far. Rename Ticket → SupportCase below to mint a
        second one, then compare them here.
      </EmptyRow>
    );
  }

  const select = (
    id: string,
    label: string,
    value: number | null,
    onChange: (n: number) => void,
  ) => (
    <NativeSelect.Root size="xs" width="auto">
      <NativeSelect.Field
        id={id}
        aria-label={label}
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.currentTarget.value))}
      >
        {numbers.map((n) => (
          <option key={n} value={n}>
            rev {n}
          </option>
        ))}
      </NativeSelect.Field>
      <NativeSelect.Indicator />
    </NativeSelect.Root>
  );

  return (
    <Stack gap="2">
      <Flex alignItems="center" gap="2" fontSize="sm">
        <Text>From</Text>
        {select("diff-from", "From revision", from, (n) =>
          setPicked((p) => ({ ...p, from: n })),
        )}
        <Text>to</Text>
        {select("diff-to", "To revision", to, (n) =>
          setPicked((p) => ({ ...p, to: n })),
        )}
        {loading ? <Spinner size="xs" /> : null}
      </Flex>
      {from === to ? (
        <EmptyRow>Pick two different revisions.</EmptyRow>
      ) : error ? (
        <StatusAlert status="error" title="Diff failed" description={error} />
      ) : data ? (
        <DiffView diff={data} />
      ) : null}
    </Stack>
  );
}

function RenameResult({
  result,
  onDismiss,
}: {
  result: RenameResponse;
  onDismiss: () => void;
}) {
  const { migration } = result;
  return (
    <Stack gap="2">
      <StatusAlert
        status={migration.errored > 0 ? "warning" : "success"}
        title={`Revision ${result.revision} is active`}
        description={`Dry run counted ${result.dry_run_total} node(s); migration ${migration.status}: ${migration.processed}/${migration.total} relabelled, ${migration.errored} errored. The app's client now resolves ${result.client.domain_id ?? "(none)"} in ${result.client.validation_mode ?? "no"} mode.`}
        onDismiss={onDismiss}
      />
      <Flex gap="2" flexWrap="wrap" fontSize="xs" color="fg.muted">
        <Text>
          version <Code size="sm">{shortId(result.version_id, 12)}</Code>
        </Text>
        <Text>
          migration <Code size="sm">{shortId(migration.id, 12)}</Code>
        </Text>
      </Flex>
      <DiffView diff={result.diff} />
    </Stack>
  );
}

/**
 * The stored `support-desk` ontology on bolt: the active revision, what the
 * app's own client resolved, every revision (with activation), a diff
 * between any two, label counts, and the Ticket → SupportCase migration.
 */
export function OntologyPanel({ refreshKey, onOntologyChanged }: OntologyPanelProps) {
  const load = useCallback(
    (signal: AbortSignal) => api.ontology.overview({ signal }),
    [],
  );
  const { data, error, loading, reload } = useApi(load, refreshKey);

  const [renameOpen, setRenameOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameResult, setRenameResult] = useState<RenameResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [activateTarget, setActivateTarget] = useState<OntologyRevision | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [activateResult, setActivateResult] = useState<ActivateResponse | null>(null);

  const active = data?.active ?? null;
  const canRename = !!active?.entity_types.some((t) => t.label === RENAME.old);

  const runRename = useCallback(async () => {
    setRenaming(true);
    setActionError(null);
    try {
      const result = await api.ontology.rename(RENAME);
      setRenameResult(result);
      setActivateResult(null);
      setRenameOpen(false);
      reload();
      onOntologyChanged();
    } catch (err) {
      setActionError(`Rename failed: ${errorMessage(err)}`);
      setRenameOpen(false);
    } finally {
      setRenaming(false);
    }
  }, [onOntologyChanged, reload]);

  const runActivate = useCallback(async () => {
    if (!activateTarget) return;
    setActivating(activateTarget.version_id);
    setActionError(null);
    try {
      const result = await api.ontology.activate(activateTarget.version_id);
      setActivateResult(result);
      setActivateTarget(null);
      reload();
      onOntologyChanged();
    } catch (err) {
      setActionError(`Activation failed: ${errorMessage(err)}`);
      setActivateTarget(null);
    } finally {
      setActivating(null);
    }
  }, [activateTarget, onOntologyChanged, reload]);

  const labelCounts = useMemo(
    () =>
      Object.entries(data?.label_counts ?? {}).sort(
        (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
      ),
    [data],
  );

  if (!data) {
    return error ? (
      <Stack gap="3">
        <StatusAlert status="error" title="Ontology unavailable" description={error} />
        <Button size="sm" variant="outline" onClick={reload} alignSelf="start">
          Try again
        </Button>
      </Stack>
    ) : (
      <LoadingRow label="Loading ontology…" />
    );
  }

  return (
    <Stack gap="6">
      <PanelSection
        title="Active ontology"
        icon={<LuBookOpen size={16} />}
        actions={
          <IconButton
            aria-label="Refresh ontology"
            size="xs"
            variant="ghost"
            onClick={reload}
            disabled={loading}
          >
            {loading ? <Spinner size="xs" /> : <LuRefreshCw />}
          </IconButton>
        }
      >
        {error ? (
          <StatusAlert status="error" title="Refresh failed" description={error} />
        ) : null}
        {actionError ? (
          <StatusAlert
            status="error"
            title="Ontology action failed"
            description={actionError}
            onDismiss={() => setActionError(null)}
          />
        ) : null}
        {activateResult ? (
          <StatusAlert
            status="success"
            title={`Revision ${activateResult.revision} activated (${activateResult.validation_mode})`}
            description={`The app's client reconnected and now resolves ${activateResult.client.domain_id ?? "(none)"} in ${activateResult.client.validation_mode ?? "no"} mode.`}
            onDismiss={() => setActivateResult(null)}
          />
        ) : null}
        <ActiveSummary overview={data} />
      </PanelSection>

      {active ? (
        <PanelSection
          title="Entity types"
          icon={<LuTags size={16} />}
          description="Each label maps onto a POLE+O type and subtype; the description is what GLiNER2.5 reads as its annotation guideline."
        >
          <Stack gap="2">
            {active.entity_types.map((type) => (
              <Box
                key={type.label}
                p="2"
                borderRadius="md"
                bg="bg.subtle"
                borderLeftWidth="3px"
                style={{ borderLeftColor: labelColor(type.label) }}
              >
                <Flex alignItems="center" gap="2">
                  <Text fontWeight="medium" fontSize="sm">
                    {type.label}
                  </Text>
                  <Text fontSize="xs" fontFamily="mono" color="fg.muted">
                    {fullType(type.pole_type, type.subtype)}
                  </Text>
                  <Badge size="xs" ml="auto" variant="outline">
                    {data.label_counts[type.label] ?? 0} nodes
                  </Badge>
                </Flex>
                {type.description ? (
                  <Text fontSize="xs" color="fg.muted" mt="1">
                    {type.description}
                  </Text>
                ) : null}
              </Box>
            ))}
          </Stack>
          {active.relationships.length > 0 ? (
            <List.Root gap="0.5" variant="plain" fontSize="xs" fontFamily="mono">
              {active.relationships.map((rel) => (
                <List.Item key={`${rel.source}-${rel.type}-${rel.target}`}>
                  ({rel.source})-[:{rel.type}]-&gt;({rel.target})
                </List.Item>
              ))}
            </List.Root>
          ) : null}
        </PanelSection>
      ) : null}

      <PanelSection
        title="Label counts"
        icon={<LuTags size={16} />}
        description="Every label any revision declares, and how many :Entity nodes carry it now."
      >
        {labelCounts.length === 0 ? (
          <EmptyRow>No labels declared yet.</EmptyRow>
        ) : (
          <Flex gap="2" flexWrap="wrap">
            {labelCounts.map(([label, count]) => (
              <Badge
                key={label}
                size="md"
                variant="outline"
                opacity={count === 0 ? 0.6 : 1}
              >
                <Box
                  w="2"
                  h="2"
                  borderRadius="full"
                  style={{ background: labelColor(label) }}
                />
                {label}
                <Text as="span" fontWeight="bold">
                  {count}
                </Text>
              </Badge>
            ))}
          </Flex>
        )}
      </PanelSection>

      <PanelSection
        title="Revise: Ticket → SupportCase"
        icon={<LuReplace size={16} />}
        description="Mints a new revision with the label renamed and strict mode, diffs it, dry-runs then runs the migration that relabels existing nodes, activates it and reconnects the app's client."
      >
        <Button
          size="sm"
          colorPalette="orange"
          alignSelf="start"
          onClick={() => setRenameOpen(true)}
          disabled={!canRename || renaming}
        >
          <LuReplace />
          Rename Ticket → SupportCase and migrate
        </Button>
        {!canRename && active ? (
          <Text fontSize="xs" color="fg.muted">
            The active revision does not declare <Code size="sm">Ticket</Code>
            {active.entity_types.some((t) => t.label === RENAME.new)
              ? " — it has already been renamed to SupportCase."
              : "."}
          </Text>
        ) : null}
        {renameResult ? (
          <RenameResult
            result={renameResult}
            onDismiss={() => setRenameResult(null)}
          />
        ) : null}
      </PanelSection>

      <PanelSection
        title="Revisions"
        icon={<LuHistory size={16} />}
        description="Activation is per database: exactly one revision is active, and every client that connects afterwards extracts against it."
      >
        {data.revisions.length === 0 ? (
          <EmptyRow>No stored revisions.</EmptyRow>
        ) : (
          <RevisionsTable
            revisions={data.revisions}
            busyVersionId={activating}
            onActivate={setActivateTarget}
          />
        )}
      </PanelSection>

      <PanelSection title="Compare revisions" icon={<LuGitCompare size={16} />}>
        <DiffExplorer revisions={data.revisions} />
      </PanelSection>

      <ConfirmDialog
        open={renameOpen}
        title="Rename Ticket → SupportCase and migrate?"
        confirmLabel="Rename, migrate and activate"
        confirmPalette="orange"
        loading={renaming}
        onConfirm={runRename}
        onClose={() => setRenameOpen(false)}
      >
        <Stack gap="2">
          <Text>This runs the whole revision cycle against the database:</Text>
          <List.Root ps="5" gap="1">
            <List.Item>
              creates a new revision with <Code size="sm">Ticket</Code> renamed
              to <Code size="sm">SupportCase</Code>;
            </List.Item>
            <List.Item>
              switches validation to <b>strict</b> — undeclared entities are
              dropped on ingest and writes that break the ontology raise;
            </List.Item>
            <List.Item>
              dry-runs, then migrates the existing <Code size="sm">:Ticket</Code>{" "}
              nodes to <Code size="sm">:SupportCase</Code>;
            </List.Item>
            <List.Item>activates the new revision and reconnects the app.</List.Item>
          </List.Root>
          <Text color="fg.muted">
            Activation is <b>per database</b>: every client connected to this
            Neo4j database picks up the new revision on its next connect. Use
            the Revisions table to go back to revision 1 (the relabelled nodes
            stay relabelled).
          </Text>
        </Stack>
      </ConfirmDialog>

      <ConfirmDialog
        open={activateTarget !== null}
        title={`Activate revision ${activateTarget?.revision ?? ""}?`}
        confirmLabel="Activate"
        loading={activating !== null}
        onConfirm={runActivate}
        onClose={() => setActivateTarget(null)}
      >
        <Text>
          Revision {activateTarget?.revision} ({activateTarget?.validation_mode})
          becomes the active ontology for the whole database, and the app’s
          client reconnects to pick it up. Existing nodes are not relabelled.
        </Text>
      </ConfirmDialog>
    </Stack>
  );
}
