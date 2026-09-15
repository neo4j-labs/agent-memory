/** Private, durable state for the hosted tutorials; no SDK-wide behavior changes. */
import { createHash, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { MemoryClient, type Conversation, type Entity, type Message } from "@neo4j-labs/agent-memory";

export interface TutorialConfig { endpoint: string; apiKey: string; workspaceId?: string }
export type ResourceKind = "conversation" | "message" | "entity";
export interface TutorialResource {
  kind: ResourceKind; id: string; disposition: "present" | "deleted" | "retained";
  parentId?: string; role?: string; contentSha256?: string; name?: string;
  createdAt?: string; merged?: boolean; origin?: "explicit" | "derived" | "recorded";
  sourceIds?: string[]; reason?: string;
}
export interface TutorialOperation {
  id: string; kind: string; parentId?: string; status: "pending" | "uncertain" | "done";
  returnedIds: string[];
}
export interface TutorialState {
  schemaVersion: 1; lesson: string; runId: string; createdAt: string;
  identity: { endpoint: string; workspaceId?: string; credentialSha256: string };
  resources: TutorialResource[]; operations: TutorialOperation[];
  cleanup?: { prepared: boolean; complete: boolean; residualIds: string[]; error?: string };
}
export const sha256 = (text: string): string => createHash("sha256").update(text).digest("hex");
function normalizeConfig(config: TutorialConfig): TutorialConfig {
  const apiKey = config.apiKey;
  if (!apiKey?.trim()) throw new Error("Set MEMORY_API_KEY before using tutorial state.");
  const url = new URL(config.endpoint);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("MEMORY_ENDPOINT must be an HTTP(S) endpoint without credentials, query, or fragment.");
  }
  return { endpoint: url.href.replace(/\/+$/, ""), apiKey,
    ...(config.workspaceId ? { workspaceId: config.workspaceId } : {}) };
}
export function resolveTutorialConfig(env: NodeJS.ProcessEnv = process.env): TutorialConfig {
  return normalizeConfig({ endpoint: env.MEMORY_ENDPOINT ?? "https://memory.neo4jlabs.com/v1", apiKey: env.MEMORY_API_KEY ?? "",
    ...(env.MEMORY_WORKSPACE_ID ? { workspaceId: env.MEMORY_WORKSPACE_ID } : {}) });
}
function identity(config: TutorialConfig): TutorialState["identity"] {
  return { endpoint: config.endpoint, workspaceId: config.workspaceId, credentialSha256: sha256(config.apiKey) };
}
function nonempty(value: unknown): value is string { return typeof value === "string" && value.length > 0; }
function validateState(value: unknown): asserts value is TutorialState {
  const s = value as TutorialState;
  if (!s || s.schemaVersion !== 1 || !nonempty(s.lesson) || !/^[0-9a-f-]{36}$/.test(s.runId) ||
      !Number.isFinite(Date.parse(s.createdAt)) || !s.identity || !nonempty(s.identity.endpoint) ||
      !/^[0-9a-f]{64}$/.test(s.identity.credentialSha256) ||
      (s.identity.workspaceId !== undefined && !nonempty(s.identity.workspaceId)) ||
      !Array.isArray(s.resources) || !Array.isArray(s.operations)) throw new Error("Invalid tutorial state; keep it for inspection, do not reseed.");
  const keys = new Set<string>();
  for (const r of s.resources) {
    const key = `${r.kind}:${r.id}`;
    if (!["conversation", "message", "entity"].includes(r.kind) || !nonempty(r.id) || keys.has(key) ||
        !["present", "deleted", "retained"].includes(r.disposition) ||
        (r.kind === "message" && (!nonempty(r.parentId) || !nonempty(r.role) || !/^[a-f0-9]{64}$/.test(r.contentSha256 ?? ""))) ||
        (r.sourceIds !== undefined && (!Array.isArray(r.sourceIds) || !r.sourceIds.every(nonempty)))) throw new Error("Invalid tutorial resource record.");
    keys.add(key);
  }
  if (s.resources.filter(r => r.kind === "conversation").length > 1 ||
      s.resources.some(r => r.kind === "message" && !s.resources.some(c => c.kind === "conversation" && c.id === r.parentId))) throw new Error("Invalid tutorial resource ownership.");
  for (const op of s.operations) if (!nonempty(op.id) || !nonempty(op.kind) || !["pending", "uncertain", "done"].includes(op.status) || !Array.isArray(op.returnedIds) || !op.returnedIds.every(nonempty)) throw new Error("Invalid tutorial operation record.");
  if (s.cleanup && (typeof s.cleanup.prepared !== "boolean" || typeof s.cleanup.complete !== "boolean" || !Array.isArray(s.cleanup.residualIds) || !s.cleanup.residualIds.every(nonempty))) throw new Error("Invalid cleanup state.");
}
function atomicWrite(path: string, state: TutorialState, create: boolean, expected?: string): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  if (existsSync(path) && lstatSync(path).isSymbolicLink()) throw new Error("Refusing a symbolic-link state file.");
  // An exclusive lock prevents another tutorial process from silently losing an update.
  const lock = `${path}.lock`; const fd = openSync(lock, "wx", 0o600);
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    if (expected !== undefined && readFileSync(path, "utf8") !== expected) throw new Error("Tutorial state changed in another process. Reload it before continuing.");
    if (create && existsSync(path)) throw new Error("State already exists; inspect or clean up the existing run instead of reseeding.");
    const output = openSync(temp, "wx", 0o600);
    try { writeFileSync(output, JSON.stringify(state, null, 2) + "\n"); fsyncSync(output); } finally { closeSync(output); }
    renameSync(temp, path);
  } finally { closeSync(fd); unlinkSync(lock); if (existsSync(temp)) unlinkSync(temp); }
}
export class TutorialRun {
  readonly path: string;
  readonly client: MemoryClient;
  private persisted: string;
  private constructor(path: string, readonly state: TutorialState, readonly config: TutorialConfig) {
    this.path = resolve(path); this.persisted = readFileSync(this.path, "utf8");
    this.client = new MemoryClient({ ...config, transport: "rest", timeout: 30_000 });
  }
  static create(path: string, lesson: string, config = resolveTutorialConfig()): TutorialRun {
    config = normalizeConfig(config);
    if (!path.endsWith(".json")) throw new Error("Use a private .json tutorial state file.");
    const state: TutorialState = { schemaVersion: 1, lesson, runId: randomUUID(), createdAt: new Date().toISOString(), identity: identity(config), resources: [], operations: [] };
    atomicWrite(resolve(path), state, true); return new TutorialRun(path, state, config);
  }
  static load(path: string, config = resolveTutorialConfig(), lesson?: string): TutorialRun {
    config = normalizeConfig(config);
    if (lstatSync(path).isSymbolicLink()) throw new Error("Refusing a symbolic-link state file.");
    const state: unknown = JSON.parse(readFileSync(path, "utf8")); validateState(state);
    const expected = identity(config);
    if (state.identity.endpoint !== expected.endpoint || state.identity.workspaceId !== expected.workspaceId || state.identity.credentialSha256 !== expected.credentialSha256) {
      throw new Error("Tutorial endpoint, workspace, or credential changed. Stop and verify ownership with the key owner; do not silently rebind saved IDs.");
    }
    if (lesson && state.lesson !== lesson) throw new Error("This state belongs to a different tutorial.");
    return new TutorialRun(path, state, config);
  }
  get userId(): string { return `tutorial-${this.state.lesson}-${this.state.runId}`; }
  get name(): string { return `Lantern Orchard ${this.state.runId}`; }
  get conversationId(): string {
    const record = this.state.resources.find(r => r.kind === "conversation");
    if (!record) throw new Error("No conversation ID is recorded. Inspect partial state; do not reseed it.");
    return record.id;
  }
  save(): void {
    atomicWrite(this.path, this.state, false, this.persisted); this.persisted = readFileSync(this.path, "utf8");
  }
  inspect(): object {
    return { schemaVersion: 1, lesson: this.state.lesson, runId: this.state.runId, createdAt: this.state.createdAt,
      endpoint: this.config.endpoint, workspaceConfigured: Boolean(this.config.workspaceId), userId: this.userId, entityName: this.name,
      resources: this.state.resources, operations: this.state.operations, cleanup: this.state.cleanup };
  }
  retain(resource: TutorialResource): void {
    const existing = this.state.resources.find(r => r.kind === resource.kind && r.id === resource.id);
    if (existing) {
      if (existing.parentId !== resource.parentId || (existing.contentSha256 && existing.contentSha256 !== resource.contentSha256)) throw new Error("Returned resource conflicts with the saved record.");
      return;
    }
    this.state.resources.push(resource);
    try { this.save(); } catch (error) { throw new Error(`Could not save returned ${resource.kind} ID ${resource.id}. Retain this exact ID for recovery.`, { cause: error }); }
  }
  async mutation<T>(kind: string, action: () => Promise<T>, record: (value: T) => string[], parentId?: string): Promise<T> {
    if (this.state.cleanup) throw new Error("Cleanup has begun; no further tutorial writes are allowed.");
    if (this.state.operations.some(op => op.status !== "done")) throw new Error("An earlier write outcome is uncertain; inspect and record its returned IDs before continuing.");
    const op: TutorialOperation = { id: randomUUID(), kind, parentId, status: "pending", returnedIds: [] };
    this.state.operations.push(op); this.save();
    try { const result = await action(); op.returnedIds = record(result); op.status = "done"; this.save(); return result; }
    catch (error) {
      op.status = "uncertain";
      try { this.save(); } catch (saveError) { throw new AggregateError([error, saveError], "Write outcome is uncertain and state could not be saved. Preserve both errors and any returned IDs."); }
      throw error;
    }
  }
  async createConversation(): Promise<Conversation> {
    if (this.state.resources.some(r => r.kind === "conversation")) throw new Error("This run already has a conversation; verify or resume it instead of reseeding.");
    return this.mutation("create-conversation", () => this.client.shortTerm.createConversation({ userId: this.userId, metadata: { run: this.state.runId } }), c => {
      this.retain({ kind: "conversation", id: c.id, disposition: "present", createdAt: c.createdAt }); return [c.id];
    });
  }
  recordMessage(message: Message, conversationId = this.conversationId): void {
    if (conversationId !== this.conversationId) throw new Error("Message belongs to an unrecorded conversation.");
    this.retain({ kind: "message", id: message.id, parentId: conversationId, role: message.role,
      contentSha256: sha256(message.content), createdAt: message.timestamp, disposition: "present" });
  }
  async addMessage(role: "user" | "assistant" | "system", content: string): Promise<Message> {
    return this.mutation("add-message", () => this.client.shortTerm.addMessage(this.conversationId, role, content), m => { this.recordMessage(m); return [m.id]; }, this.conversationId);
  }
  async bulkAddMessages(messages: Array<{ role: "user"; content: string }>): Promise<Message[]> {
    return this.mutation("bulk-add-messages", () => this.client.shortTerm.bulkAddMessages(this.conversationId, messages), result => { for (const m of result) this.recordMessage(m); return result.map(m => m.id); }, this.conversationId);
  }
  async addEntity(name = this.name): Promise<Entity> {
    return this.mutation("add-entity", () => this.client.longTerm.addEntity(name, "organization", { description: `Fictional organization for tutorial run ${this.state.runId}.` }), e => {
      this.retain({ kind: "entity", id: e.id, name: e.name, createdAt: e.createdAt, merged: Boolean(e.metadata?.nams_resolution), origin: "explicit", disposition: "present" }); return [e.id];
    });
  }
  /** Track returned middleware write IDs before they are hidden by its best-effort API. */
  trackMiddlewareWrites(): () => void {
    const original = this.client.shortTerm.addMessage.bind(this.client.shortTerm);
    this.client.shortTerm.addMessage = async (...args) => {
      if (args[0] !== this.conversationId) throw new Error("Middleware must use the recorded conversation.");
      return this.mutation("middleware-message", () => original(...args), m => { this.recordMessage(m); return [m.id]; }, this.conversationId);
    };
    return () => { this.client.shortTerm.addMessage = original; };
  }
  async ownedConversation(): Promise<Conversation> {
    const conversation = await this.client.shortTerm.getConversationMetadata(this.conversationId);
    if (conversation.userId !== this.userId || conversation.metadata?.run !== this.state.runId) throw new Error("Conversation ownership does not match this run.");
    return conversation;
  }
  async verify(): Promise<{ messages: number; conversationId: string }> {
    await this.ownedConversation();
    const actual = await this.client.shortTerm.getConversation(this.conversationId);
    const recorded = this.state.resources.filter(r => r.kind === "message" && r.disposition !== "deleted");
    if (actual.messages.length !== recorded.length || actual.messages.some(m => !recorded.some(r => r.id === m.id && r.role === m.role && r.contentSha256 === sha256(m.content)))) {
      throw new Error("Conversation contains missing, changed, or unrecorded messages. Inspect and record exact returned IDs before cleanup.");
    }
    if (this.state.operations.some(op => op.status !== "done")) throw new Error("A write outcome remains uncertain; verified messages alone cannot resolve it.");
    return { messages: actual.messages.length, conversationId: this.conversationId };
  }
  async verifyTurn(beforeIds: readonly string[], userText: string, assistantText: string): Promise<void> {
    await this.ownedConversation();
    const conversation = await this.client.shortTerm.getConversation(this.conversationId);
    const added = conversation.messages.filter(message => !beforeIds.includes(message.id));
    const user = added.find(message => message.role === "user" && message.content === userText);
    const assistant = added.find(message => message.role === "assistant" && message.content === assistantText);
    if (!user || !assistant || user.id === assistant.id) throw new Error("Model output succeeded, but new user/assistant persistence was not verified. Keep this state for inspection.");
    this.recordMessage(user); this.recordMessage(assistant);
    await this.verify();
  }
  async inspectMessages(): Promise<object> {
    await this.ownedConversation();
    const conversation = await this.client.shortTerm.getConversation(this.conversationId);
    return conversation.messages.map(message => ({ id: message.id, role: message.role, contentSha256: sha256(message.content),
      recorded: this.state.resources.some(resource => resource.kind === "message" && resource.id === message.id) }));
  }
  async request(path: string, timeoutMs = 30_000): Promise<unknown> {
    const response = await fetch(`${this.config.endpoint}${path}`, { headers: {
      Authorization: `Bearer ${this.config.apiKey}`, ...(this.config.workspaceId ? { "X-Workspace-Id": this.config.workspaceId } : {}),
    }, signal: AbortSignal.timeout(timeoutMs), redirect: "error" });
    if (!response.ok) throw new Error(`Tutorial request returned HTTP ${response.status}.`);
    return response.json();
  }
}

/** Resolve symlinked checkout/temp paths before deciding whether a file is the CLI entry. */
export function isTutorialEntryPoint(moduleUrl: string): boolean {
  return Boolean(process.argv[1] && moduleUrl === pathToFileURL(realpathSync(process.argv[1])).href);
}
