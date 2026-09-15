/**
 * NAMS ontology surface — typed, versioned, validated domain schemas
 * extending POLE+O. Mirrors the Python `client.ontology` accessor.
 *
 * The ontology endpoints use the service’s snake_case contract. The active
 * response carries its bound version, which can be older than the latest
 * revision. `RestTransport` sends these bodies verbatim via `snakeBody`.
 *
 *   GET    /ontologies                        → list (summaries)
 *   GET    /ontologies/{id}                    → { record, versions[] }
 *   GET    /ontologies/active                  → { ontology, version }
 *   POST   /ontologies/{name}/clone            → version
 *   POST   /ontologies        { ontology, validation_mode? }       → version
 *   PUT    /ontologies/{id}    { ontology, validation_mode? }       → new revision
 *   POST   /ontologies/active { version_id }                       → version
 *   DELETE /ontologies/{id}                    → 204
 */

import { NotSupportedError } from "../errors.js";
import type { Transport } from "../transport/index.js";

export interface PropertyDef {
  name: string;
  type: string; // string | datetime | date | float | integer
  required?: boolean;
  unique?: boolean;
  enum?: string[];
}

export interface EntityTypeDef {
  label: string;
  poleType: string; // PERSON | ORGANIZATION | LOCATION | EVENT | OBJECT
  subtype?: string;
  color?: string;
  icon?: string;
  properties: PropertyDef[];
}

export interface RelationshipDef {
  type: string;
  source: string;
  target: string;
}

export interface DomainInfo {
  id: string;
  name: string;
  description?: string;
  tagline?: string;
  emoji?: string;
}

export interface OntologyDocument {
  domain: DomainInfo;
  entityTypes: EntityTypeDef[];
  relationships: RelationshipDef[];
}

export interface OntologySummary {
  id: string;
  name: string;
  displayName?: string;
  description?: string;
  emoji?: string;
  tagline?: string;
  isSystem: boolean;
  currentRevision?: number;
  isActive: boolean;
}

export interface OntologyVersion {
  id: string;
  ontologyId: string;
  revision: number;
  validationMode: string; // permissive | strict
  document?: OntologyDocument;
  schemaHash?: string;
  createdAt?: string;
  message?: string;
}

export interface OntologyRecord {
  id: string;
  name: string;
  description?: string;
  workspaceId?: string;
  isSystem: boolean;
  createdAt?: string;
}

export interface Ontology {
  record: OntologyRecord;
  versions: OntologyVersion[];
}

/** Legacy document-only responses leave the binding metadata absent. */
export interface ActiveOntology {
  document: OntologyDocument;
  validationMode?: string;
  revision?: number;
  ontologyId?: string;
  versionId?: string;
  schemaHash?: string;
}

export interface CreateOntologyOptions {
  /** Identity comes from `schema.domain`; `name` is accepted for symmetry. */
  name: string;
  schema: OntologyDocument;
  validationMode?: string;
}

export interface UpdateOntologyOptions {
  id: string;
  schema: OntologyDocument;
  validationMode?: string;
}

export interface ImportWarning {
  code?: string;
  message?: string;
  path?: string;
}

/** A non-persisted ontology draft converted from an external format. */
export interface OntologyImportResult {
  document?: OntologyDocument;
  warnings: ImportWarning[];
  detectedFormat?: string;
  suggestedName?: string;
}

export interface ImportOntologyOptions {
  /** Inline source document. Mutually exclusive with `url`. */
  content?: string;
  /** URL fetched server-side (https only, SSRF-guarded). Mutually exclusive with `content`. */
  url?: string;
  /** Explicit converter id (e.g. "arrows", "rdf") or "auto"/undefined to detect. */
  format?: string;
}

/** Structural diff between two ontology revisions. */
export interface OntologyDiff {
  fromRevision?: number;
  toRevision?: number;
  /** `{ added, removed, renamed, modified }` — passed through. */
  entityTypes: Record<string, unknown>;
  relationships: Record<string, unknown>;
  modeChange?: Record<string, unknown> | null;
}

export interface TypeMapping {
  from: string;
  to: string;
}

export interface MigrateOptions {
  fromVersionId: string;
  toVersionId: string;
  typeMappings: TypeMapping[];
  /** Report counts without mutating the graph. */
  dryRun?: boolean;
  /** Per-transaction node cap (0 → server default 1000). */
  batchSize?: number;
}

/** An asynchronous label-rename migration job. */
export interface MigrationJob {
  id: string;
  ontologyId?: string;
  workspaceId?: string;
  status?: string; // pending | running | completed | failed | paused
  total?: number;
  processed?: number;
  errored?: number;
  errorMessage?: string;
  spec?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
  startedAt?: string;
  completedAt?: string;
  createdBy?: string;
}

// ---- snake_case wire shapes (responses are snake_case) ----------------------

interface WireProperty {
  name: string;
  type: string;
  required?: boolean;
  unique?: boolean;
  enum?: string[] | null;
}
interface WireEntityType {
  label: string;
  pole_type: string;
  subtype?: string;
  color?: string;
  icon?: string;
  properties?: WireProperty[] | null;
}
interface WireRelationship {
  type: string;
  source: string;
  target: string;
}
interface WireDocument {
  domain: DomainInfo;
  entity_types?: WireEntityType[] | null;
  relationships?: WireRelationship[] | null;
}
interface WireVersion {
  id: string;
  ontology_id: string;
  revision: number;
  validation_mode: string;
  schema_json?: string | null;
  schema_hash?: string;
  created_at?: string;
  message?: string;
}
interface WireSummary {
  id: string;
  name: string;
  display_name?: string;
  description?: string;
  emoji?: string;
  tagline?: string;
  is_system?: boolean;
  current_revision?: number;
  is_active?: boolean;
}
interface WireRecord {
  id: string;
  name: string;
  description?: string;
  workspace_id?: string;
  is_system?: boolean;
  created_at?: string;
}

function toDocument(raw: WireDocument | null | undefined): OntologyDocument | undefined {
  if (!raw || typeof raw !== "object" || !raw.domain) return undefined;
  return {
    domain: raw.domain,
    entityTypes: (raw.entity_types ?? []).map((e) => ({
      label: e.label,
      poleType: e.pole_type,
      subtype: e.subtype,
      color: e.color,
      icon: e.icon,
      properties: (e.properties ?? []).map((p) => ({
        name: p.name,
        type: p.type,
        required: p.required,
        unique: p.unique,
        enum: p.enum ?? undefined,
      })),
    })),
    relationships: raw.relationships ?? [],
  };
}

function toDocumentFromJson(schemaJson: string | null | undefined): OntologyDocument | undefined {
  if (schemaJson == null) return undefined;
  try {
    return toDocument(JSON.parse(schemaJson) as WireDocument);
  } catch {
    return undefined;
  }
}

function toVersion(raw: WireVersion): OntologyVersion {
  return {
    id: raw.id,
    ontologyId: raw.ontology_id,
    revision: raw.revision,
    validationMode: raw.validation_mode,
    document: toDocumentFromJson(raw.schema_json),
    schemaHash: raw.schema_hash,
    createdAt: raw.created_at,
    message: raw.message,
  };
}

/** Validate the supplied binding without tightening unrelated version parsing. */
function toActiveVersion(raw: unknown): OntologyVersion {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Invalid active ontology version metadata: expected an object.");
  }
  const value = raw as Record<string, unknown>;
  for (const key of ["id", "ontology_id"]) {
    if (typeof value[key] !== "string" || !value[key].trim()) {
      throw new Error(`Invalid active ontology version metadata: ${key} is required.`);
    }
  }
  if (typeof value.revision !== "number" || !Number.isSafeInteger(value.revision) || value.revision < 1) {
    throw new Error("Invalid active ontology version metadata: revision must be a positive integer.");
  }
  if (value.validation_mode !== "permissive" && value.validation_mode !== "strict") {
    throw new Error("Invalid active ontology version metadata: unknown validation_mode.");
  }
  for (const key of ["schema_hash", "created_at", "message"]) {
    if (value[key] != null && typeof value[key] !== "string") {
      throw new Error(`Invalid active ontology version metadata: ${key} must be a string.`);
    }
  }
  if (value.schema_json != null && typeof value.schema_json !== "string") {
    throw new Error("Invalid active ontology version metadata: schema_json must be a string.");
  }
  const version = toVersion(value as unknown as WireVersion);
  if (value.schema_json != null && !version.document) {
    throw new Error("Invalid active ontology version metadata: schema_json is not a document.");
  }
  return version;
}

/** Compare parsed fields with their defaults, independent of JSON key order. */
function normalizedDocument(doc: OntologyDocument): string {
  return JSON.stringify({
    domain: {
      id: doc.domain.id,
      name: doc.domain.name,
      description: doc.domain.description ?? null,
      tagline: doc.domain.tagline ?? null,
      emoji: doc.domain.emoji ?? null,
    },
    entityTypes: doc.entityTypes.map((e) => ({
      label: e.label,
      poleType: e.poleType,
      subtype: e.subtype ?? null,
      color: e.color ?? null,
      icon: e.icon ?? null,
      properties: e.properties.map((p) => ({
        name: p.name,
        type: p.type,
        required: p.required ?? false,
        unique: p.unique ?? false,
        enum: p.enum ?? null,
      })),
    })),
    relationships: doc.relationships.map((r) => ({ type: r.type, source: r.source, target: r.target })),
  });
}

function toSummary(raw: WireSummary): OntologySummary {
  return {
    id: raw.id,
    name: raw.name,
    displayName: raw.display_name,
    description: raw.description,
    emoji: raw.emoji,
    tagline: raw.tagline,
    isSystem: raw.is_system ?? false,
    currentRevision: raw.current_revision,
    isActive: raw.is_active ?? false,
  };
}

function docToWire(doc: OntologyDocument): WireDocument {
  return {
    domain: doc.domain,
    entity_types: doc.entityTypes.map((e) => ({
      label: e.label,
      pole_type: e.poleType,
      subtype: e.subtype,
      color: e.color,
      icon: e.icon,
      properties: (e.properties ?? []).map((p) => ({
        name: p.name,
        type: p.type,
        required: p.required,
        unique: p.unique,
        enum: p.enum,
      })),
    })),
    relationships: doc.relationships,
  };
}

interface WireImportResult {
  ontology?: WireDocument | null;
  warnings?: { code?: string; message?: string; path?: string }[] | null;
  detected_format?: string;
  suggested_name?: string;
}

interface WireDiff {
  from_revision?: number;
  to_revision?: number;
  entity_types?: Record<string, unknown> | null;
  relationships?: Record<string, unknown> | null;
  mode_change?: Record<string, unknown> | null;
}

interface WireMigrationJob {
  id: string;
  ontology_id?: string;
  workspace_id?: string;
  status?: string;
  total?: number;
  processed?: number;
  errored?: number;
  error_message?: string;
  spec?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  completed_at?: string;
  created_by?: string;
}

function toMigrationJob(raw: WireMigrationJob): MigrationJob {
  return {
    id: raw.id,
    ontologyId: raw.ontology_id,
    workspaceId: raw.workspace_id,
    status: raw.status,
    total: raw.total,
    processed: raw.processed,
    errored: raw.errored,
    errorMessage: raw.error_message,
    spec: raw.spec,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    startedAt: raw.started_at,
    completedAt: raw.completed_at,
    createdBy: raw.created_by,
  };
}

export class OntologyClient {
  constructor(private readonly transport: Transport) {}

  async list(): Promise<OntologySummary[]> {
    const raw = await this.transport.request<{ ontologies?: WireSummary[] }>(
      "list_ontologies",
      {},
    );
    return (raw.ontologies ?? []).map(toSummary);
  }

  async get(id: string): Promise<Ontology> {
    const raw = await this.transport.request<{ record: WireRecord; versions?: WireVersion[] }>(
      "get_ontology",
      { id },
    );
    const r = raw.record ?? ({} as WireRecord);
    return {
      record: {
        id: r.id,
        name: r.name,
        description: r.description,
        workspaceId: r.workspace_id,
        isSystem: r.is_system ?? false,
        createdAt: r.created_at,
      },
      versions: (raw.versions ?? []).map(toVersion),
    };
  }

  /** Return the exact active binding; malformed or conflicting metadata throws. */
  async getActive(): Promise<ActiveOntology> {
    const raw = await this.transport.request<{ ontology?: WireDocument; version?: unknown }>(
      "get_active_ontology",
      {},
    );
    const document = toDocument(raw.ontology);
    if (!document) {
      throw new NotSupportedError("No active ontology bound for this workspace.");
    }
    if (raw.version == null) return { document };
    const version = toActiveVersion(raw.version);
    if (version.document && normalizedDocument(version.document) !== normalizedDocument(document)) {
      throw new Error("Active ontology version schema conflicts with the active document.");
    }
    return {
      document,
      ontologyId: version.ontologyId,
      versionId: version.id,
      revision: version.revision,
      validationMode: version.validationMode,
      schemaHash: version.schemaHash,
    };
  }

  async clone(templateName: string): Promise<OntologyVersion> {
    const raw = await this.transport.request<WireVersion>("clone_ontology", { name: templateName });
    return toVersion(raw);
  }

  async create(options: CreateOntologyOptions): Promise<OntologyVersion> {
    const body: Record<string, unknown> = { ontology: docToWire(options.schema) };
    if (options.validationMode !== undefined) body.validation_mode = options.validationMode;
    const raw = await this.transport.request<WireVersion>("create_ontology", { body });
    return toVersion(raw);
  }

  async update(options: UpdateOntologyOptions): Promise<OntologyVersion> {
    const body: Record<string, unknown> = { ontology: docToWire(options.schema) };
    if (options.validationMode !== undefined) body.validation_mode = options.validationMode;
    const raw = await this.transport.request<WireVersion>("update_ontology", {
      id: options.id,
      body,
    });
    return toVersion(raw);
  }

  async activate(versionId: string): Promise<OntologyVersion> {
    const raw = await this.transport.request<WireVersion>("activate_ontology", {
      body: { version_id: versionId },
    });
    return toVersion(raw);
  }

  async delete(id: string): Promise<void> {
    await this.transport.request("delete_ontology", { id });
  }

  /**
   * Convert an external graph/ontology document (Arrows / Neo4j Data Importer /
   * RDF / GraphQL / Cypher / LinkML / native) into a non-persisted draft.
   * Supply exactly one of `content` or `url`. Persist via {@link create}
   * (pass `result.document`). Extraction-backed formats and URL fetches are
   * rate-limited per workspace.
   */
  async import(options: ImportOntologyOptions): Promise<OntologyImportResult> {
    if (!options.content && !options.url) {
      throw new Error("import requires either content or url.");
    }
    const body: Record<string, unknown> = {};
    if (options.format !== undefined) body.format = options.format;
    if (options.content !== undefined) body.content = options.content;
    if (options.url !== undefined) body.url = options.url;
    const raw = await this.transport.request<WireImportResult>("import_ontology", { body });
    return {
      document: toDocument(raw.ontology),
      warnings: (raw.warnings ?? []).map((w) => ({
        code: w.code,
        message: w.message,
        path: w.path,
      })),
      detectedFormat: raw.detected_format,
      suggestedName: raw.suggested_name,
    };
  }

  /** Diff two revisions of an ontology (added / removed / renamed / modified). */
  async diff(id: string, fromRevision: number, toRevision: number): Promise<OntologyDiff> {
    const raw = await this.transport.request<WireDiff>("diff_ontology", {
      id,
      from: fromRevision,
      to: toRevision,
    });
    return {
      fromRevision: raw.from_revision,
      toRevision: raw.to_revision,
      entityTypes: raw.entity_types ?? {},
      relationships: raw.relationships ?? {},
      modeChange: raw.mode_change ?? null,
    };
  }

  /**
   * Enqueue an asynchronous label-rename migration; returns the job. Poll
   * {@link getMigration} for status. With `dryRun: true` the job reports
   * counts without mutating the graph.
   */
  async migrate(id: string, options: MigrateOptions): Promise<MigrationJob> {
    const spec: Record<string, unknown> = {
      from_version_id: options.fromVersionId,
      to_version_id: options.toVersionId,
      type_mappings: options.typeMappings.map((m) => ({ from: m.from, to: m.to })),
      dry_run: options.dryRun ?? false,
    };
    if (options.batchSize !== undefined) spec.batch_size = options.batchSize;
    const raw = await this.transport.request<WireMigrationJob>("migrate_ontology", {
      id,
      body: { spec },
    });
    return toMigrationJob(raw);
  }

  /** Return one migration job's current status. */
  async getMigration(jobId: string): Promise<MigrationJob> {
    const raw = await this.transport.request<WireMigrationJob>("get_ontology_migration", {
      jobId,
    });
    return toMigrationJob(raw);
  }
}
