"""Cypher query templates for memory operations."""

# =============================================================================
# SHORT-TERM MEMORY QUERIES
# =============================================================================

GET_LAST_MESSAGE = """
MATCH (c:Conversation {id: $conversation_id})-[:HAS_MESSAGE]->(m:Message)
WHERE NOT (m)-[:NEXT_MESSAGE]->()
RETURN m
LIMIT 1
"""

MIGRATE_MESSAGE_LINKS = """
MATCH (c:Conversation)
MATCH (c)-[:HAS_MESSAGE]->(m:Message)
WITH c, m ORDER BY m.timestamp ASC
WITH c, collect(m) AS messages
WHERE size(messages) > 0
WITH c, messages, head(messages) AS firstMsg
MERGE (c)-[:FIRST_MESSAGE]->(firstMsg)
WITH c, messages
UNWIND range(0, size(messages) - 2) AS i
WITH c, messages[i] AS prev, messages[i + 1] AS next
MERGE (prev)-[:NEXT_MESSAGE]->(next)
WITH c, count(*) AS links
RETURN c.id AS conversation_id, links + 1 AS messages_linked
"""

CREATE_CONVERSATION = """
CREATE (c:Conversation {
    id: $id,
    session_id: $session_id,
    title: $title,
    created_at: datetime(),
    updated_at: datetime()
})
RETURN c
"""

GET_CONVERSATION = """
MATCH (c:Conversation {id: $id})
RETURN c
"""

GET_CONVERSATION_BY_SESSION = """
MATCH (c:Conversation {session_id: $session_id})
RETURN c
ORDER BY c.created_at DESC
LIMIT 1
"""

LIST_CONVERSATIONS = """
MATCH (c:Conversation {session_id: $session_id})
RETURN c
ORDER BY c.updated_at DESC
LIMIT $limit
"""

CREATE_MESSAGE = """
MATCH (c:Conversation {id: $conversation_id})
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(last:Message)
WHERE NOT (last)-[:NEXT_MESSAGE]->()
CREATE (m:Message {
    id: $id,
    role: $role,
    content: $content,
    embedding: $embedding,
    timestamp: datetime(),
    metadata: $metadata
})
CREATE (c)-[:HAS_MESSAGE]->(m)
FOREACH (_ IN CASE WHEN last IS NOT NULL THEN [1] ELSE [] END |
    CREATE (last)-[:NEXT_MESSAGE]->(m)
)
FOREACH (_ IN CASE WHEN last IS NULL THEN [1] ELSE [] END |
    CREATE (c)-[:FIRST_MESSAGE]->(m)
)
SET c.updated_at = datetime()
RETURN m
"""

CREATE_MESSAGES_BATCH = """
UNWIND $messages AS msg
MATCH (c:Conversation {id: $conversation_id})
CREATE (m:Message {
    id: msg.id,
    role: msg.role,
    content: msg.content,
    embedding: msg.embedding,
    timestamp: CASE WHEN msg.timestamp IS NOT NULL THEN datetime(msg.timestamp) ELSE datetime() END,
    metadata: msg.metadata
})
CREATE (c)-[:HAS_MESSAGE]->(m)
WITH c, count(m) AS created
SET c.updated_at = datetime()
RETURN created
"""

CREATE_MESSAGE_LINKS = """
// Link messages in order based on the provided message_ids list
// If previous_last_id is provided, link it to the first message
// If create_first_message is true, create FIRST_MESSAGE relationship
MATCH (c:Conversation {id: $conversation_id})
WITH c, $message_ids AS ids, $previous_last_id AS prevLastId, $create_first_message AS createFirst

// Get all messages in the order specified
UNWIND range(0, size(ids) - 1) AS idx
MATCH (m:Message {id: ids[idx]})
WITH c, collect(m) AS messages, prevLastId, createFirst
WHERE size(messages) > 0

// Get first message for linking
WITH c, messages, prevLastId, createFirst, head(messages) AS firstMsg

// Create FIRST_MESSAGE if this is a new conversation
FOREACH (_ IN CASE WHEN createFirst THEN [1] ELSE [] END |
    MERGE (c)-[:FIRST_MESSAGE]->(firstMsg)
)

// Link from previous last message to first of this batch
WITH c, messages, prevLastId, firstMsg
OPTIONAL MATCH (prevLast:Message {id: prevLastId})
WITH c, messages, prevLast, firstMsg
FOREACH (_ IN CASE WHEN prevLast IS NOT NULL THEN [1] ELSE [] END |
    CREATE (prevLast)-[:NEXT_MESSAGE]->(firstMsg)
)

// Create NEXT_MESSAGE chain within the batch
WITH c, messages
UNWIND CASE WHEN size(messages) > 1 THEN range(0, size(messages) - 2) ELSE [] END AS i
WITH c, messages[i] AS prev, messages[i + 1] AS next
CREATE (prev)-[:NEXT_MESSAGE]->(next)

RETURN count(*) AS linked
"""

UPDATE_MESSAGE_EMBEDDING = """
MATCH (m:Message {id: $id})
SET m.embedding = $embedding
RETURN m
"""

GET_MESSAGES_WITHOUT_EMBEDDINGS = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
WHERE m.embedding IS NULL
RETURN m.id AS id, m.content AS content
ORDER BY m.timestamp ASC
"""

GET_CONVERSATION_MESSAGES = """
MATCH (c:Conversation {id: $conversation_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m
ORDER BY m.timestamp ASC
LIMIT $limit
"""

SEARCH_MESSAGES_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('message_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node AS m, score
ORDER BY score DESC
"""

DELETE_MESSAGE = """
MATCH (m:Message {id: $id})
OPTIONAL MATCH (m)-[r:MENTIONS]->()
DELETE r, m
RETURN count(m) > 0 AS deleted
"""

DELETE_MESSAGE_NO_CASCADE = """
MATCH (m:Message {id: $id})
DELETE m
RETURN count(m) > 0 AS deleted
"""

LIST_SESSIONS = """
MATCH (c:Conversation)
WHERE $prefix IS NULL OR c.session_id STARTS WITH $prefix
WITH c
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
WITH c,
     count(m) AS message_count,
     min(m.timestamp) AS first_msg_time,
     max(m.timestamp) AS last_msg_time,
     collect(m) AS messages
WITH c, message_count, first_msg_time, last_msg_time,
     head([msg IN messages WHERE msg.timestamp = first_msg_time | msg.content]) AS first_content,
     head([msg IN messages WHERE msg.timestamp = last_msg_time | msg.content]) AS last_content
WITH c.session_id AS session_id,
     c.title AS title,
     c.created_at AS created_at,
     c.updated_at AS updated_at,
     message_count,
     substring(first_content, 0, 100) AS first_message_preview,
     substring(last_content, 0, 100) AS last_message_preview
ORDER BY
    CASE WHEN $order_by = 'created_at' AND $order_dir = 'desc' THEN created_at END DESC,
    CASE WHEN $order_by = 'created_at' AND $order_dir = 'asc' THEN created_at END ASC,
    CASE WHEN $order_by = 'updated_at' AND $order_dir = 'desc' THEN updated_at END DESC,
    CASE WHEN $order_by = 'updated_at' AND $order_dir = 'asc' THEN updated_at END ASC,
    CASE WHEN $order_by = 'message_count' AND $order_dir = 'desc' THEN message_count END DESC,
    CASE WHEN $order_by = 'message_count' AND $order_dir = 'asc' THEN message_count END ASC
SKIP $offset LIMIT $limit
RETURN session_id, title, created_at, updated_at, message_count, first_message_preview, last_message_preview
"""

# =============================================================================
# LONG-TERM MEMORY QUERIES
# =============================================================================

# NOTE: CREATE_ENTITY is now dynamically generated to support type/subtype as node labels.
# Use build_create_entity_query(entity_type, subtype) from query_builder module instead.
# This static query is kept for reference but should not be used directly.
# Example: Entity with type=OBJECT, subtype=VEHICLE will have labels (:Entity:OBJECT:VEHICLE)
#
# from neo4j_agent_memory.graph.query_builder import build_create_entity_query
# query = build_create_entity_query("OBJECT", "VEHICLE")

CREATE_ENTITY = """
MERGE (e:Entity {name: $name, type: $type})
ON CREATE SET
    e.id = $id,
    e.subtype = $subtype,
    e.canonical_name = $canonical_name,
    e.description = $description,
    e.embedding = $embedding,
    e.confidence = $confidence,
    e.created_at = datetime(),
    e.metadata = $metadata
ON MATCH SET
    e.subtype = COALESCE($subtype, e.subtype),
    e.canonical_name = COALESCE($canonical_name, e.canonical_name),
    e.description = COALESCE($description, e.description),
    e.embedding = COALESCE($embedding, e.embedding),
    e.updated_at = datetime()
RETURN e
"""

GET_ENTITY = """
MATCH (e:Entity {id: $id})
RETURN e
"""

GET_ENTITY_BY_NAME = """
MATCH (e:Entity)
WHERE e.name = $name OR e.canonical_name = $name OR $name IN COALESCE(e.aliases, [])
RETURN e
LIMIT 1
"""

SEARCH_ENTITIES_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('entity_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node AS e, score
ORDER BY score DESC
"""

SEARCH_ENTITIES_BY_TYPE = """
MATCH (e:Entity {type: $type})
RETURN e
ORDER BY e.created_at DESC
LIMIT $limit
"""

UPDATE_ENTITY_EMBEDDING = """
MATCH (e:Entity {id: $id})
SET e.embedding = $embedding
RETURN e
"""

GET_ENTITIES_WITHOUT_EMBEDDINGS = """
MATCH (e:Entity)
WHERE e.embedding IS NULL
RETURN e.id AS id, e.name AS name, e.type AS type, e.description AS description
ORDER BY e.created_at
SKIP $skip
LIMIT $limit
"""

COUNT_ENTITIES_WITHOUT_EMBEDDINGS = """
MATCH (e:Entity)
WHERE e.embedding IS NULL
RETURN count(e) AS count
"""

CREATE_PREFERENCE = """
CREATE (p:Preference {
    id: $id,
    category: $category,
    preference: $preference,
    context: $context,
    confidence: $confidence,
    embedding: $embedding,
    created_at: datetime(),
    metadata: $metadata
})
RETURN p
"""

SEARCH_PREFERENCES_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('preference_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node AS p, score
ORDER BY score DESC
"""

SEARCH_PREFERENCES_BY_CATEGORY = """
MATCH (p:Preference {category: $category})
RETURN p
ORDER BY p.confidence DESC, p.created_at DESC
LIMIT $limit
"""

CREATE_FACT = """
CREATE (f:Fact {
    id: $id,
    subject: $subject,
    predicate: $predicate,
    object: $object,
    confidence: $confidence,
    embedding: $embedding,
    valid_from: $valid_from,
    valid_until: $valid_until,
    created_at: datetime(),
    metadata: $metadata
})
RETURN f
"""

CREATE_ENTITY_RELATIONSHIP = """
MATCH (e1:Entity {id: $source_id})
MATCH (e2:Entity {id: $target_id})
MERGE (e1)-[r:RELATED_TO {type: $relation_type}]->(e2)
ON CREATE SET
    r.id = $id,
    r.description = $description,
    r.confidence = $confidence,
    r.valid_from = $valid_from,
    r.valid_until = $valid_until,
    r.created_at = datetime()
RETURN r
"""

GET_FACTS_BY_SUBJECT = """
MATCH (f:Fact)
WHERE f.subject = $subject
RETURN f
ORDER BY f.confidence DESC, f.created_at DESC
LIMIT $limit
"""

SEARCH_FACTS_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('fact_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node AS f, score
ORDER BY score DESC
"""

FIND_DUPLICATE_FACTS = """
CALL db.index.vector.queryNodes('fact_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
  AND node.subject = $subject
  AND node.predicate = $predicate
RETURN node AS f, score
ORDER BY score DESC
"""

UPDATE_FACT_CONFIDENCE = """
MATCH (f:Fact {id: $id})
SET f.confidence = CASE
    WHEN $confidence > f.confidence THEN $confidence ELSE f.confidence END,
    f.object = CASE
    WHEN $confidence > f.confidence THEN $object ELSE f.object END
RETURN f
"""

FIND_DUPLICATE_PREFERENCES = """
CALL db.index.vector.queryNodes('preference_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold
  AND node.category = $category
RETURN node AS p, score
ORDER BY score DESC
"""

GET_ENTITY_RELATIONSHIPS = """
MATCH (e:Entity {id: $entity_id})-[r:RELATED_TO]-(other:Entity)
RETURN e, r, other
"""

LINK_MESSAGE_TO_ENTITY = """
MATCH (m:Message {id: $message_id})
MATCH (e:Entity {id: $entity_id})
MERGE (m)-[r:MENTIONS]->(e)
ON CREATE SET
    r.confidence = $confidence,
    r.start_pos = $start_pos,
    r.end_pos = $end_pos
RETURN r
"""

# Create RELATED_TO relationship between entities by name (for extraction)
# This query looks up entities by name to support cross-message relations
CREATE_ENTITY_RELATION_BY_NAME = """
MATCH (source:Entity)
WHERE toLower(source.name) = toLower($source_name)
   OR toLower(source.canonical_name) = toLower($source_name)
WITH source LIMIT 1
MATCH (target:Entity)
WHERE toLower(target.name) = toLower($target_name)
   OR toLower(target.canonical_name) = toLower($target_name)
WITH source, target LIMIT 1
MERGE (source)-[r:RELATED_TO]->(target)
ON CREATE SET
    r.relation_type = $relation_type,
    r.confidence = $confidence,
    r.created_at = datetime()
ON MATCH SET
    r.confidence = CASE WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END,
    r.updated_at = datetime()
RETURN r, source.id AS source_id, target.id AS target_id
"""

# Create RELATED_TO relationship between entities by ID
CREATE_ENTITY_RELATION_BY_ID = """
MATCH (source:Entity {id: $source_id})
MATCH (target:Entity {id: $target_id})
MERGE (source)-[r:RELATED_TO]->(target)
ON CREATE SET
    r.relation_type = $relation_type,
    r.confidence = $confidence,
    r.created_at = datetime()
ON MATCH SET
    r.confidence = CASE WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END,
    r.updated_at = datetime()
RETURN r
"""

LINK_PREFERENCE_TO_ENTITY = """
MATCH (p:Preference {id: $preference_id})
MATCH (e:Entity {id: $entity_id})
MERGE (p)-[r:ABOUT]->(e)
RETURN r
"""

# =============================================================================
# REASONING MEMORY QUERIES
# =============================================================================

CREATE_REASONING_TRACE = """
CREATE (rt:ReasoningTrace {
    id: $id,
    session_id: $session_id,
    task: $task,
    task_embedding: $task_embedding,
    outcome: $outcome,
    success: $success,
    started_at: datetime(),
    completed_at: $completed_at,
    metadata: $metadata
})
RETURN rt
"""

UPDATE_REASONING_TRACE = """
MATCH (rt:ReasoningTrace {id: $id})
SET rt.outcome = $outcome,
    rt.success = $success,
    rt.completed_at = datetime()
RETURN rt
"""

CREATE_REASONING_STEP = """
MATCH (rt:ReasoningTrace {id: $trace_id})
CREATE (rs:ReasoningStep {
    id: $id,
    step_number: $step_number,
    thought: $thought,
    action: $action,
    observation: $observation,
    embedding: $embedding,
    timestamp: datetime(),
    metadata: $metadata
})
CREATE (rt)-[:HAS_STEP {order: $step_number}]->(rs)
RETURN rs
"""

CREATE_TOOL_CALL = """
MATCH (rs:ReasoningStep {id: $step_id})
MERGE (t:Tool {name: $tool_name})
ON CREATE SET t.created_at = datetime(),
              t.total_calls = 0,
              t.successful_calls = 0,
              t.failed_calls = 0,
              t.total_duration_ms = 0
CREATE (tc:ToolCall {
    id: $id,
    tool_name: $tool_name,
    arguments: $arguments,
    result: $result,
    status: $status,
    duration_ms: $duration_ms,
    error: $error,
    timestamp: datetime()
})
CREATE (rs)-[:USES_TOOL]->(tc)
CREATE (tc)-[:INSTANCE_OF]->(t)
WITH t, tc
SET t.total_calls = coalesce(t.total_calls, 0) + 1,
    t.successful_calls = coalesce(t.successful_calls, 0) + CASE WHEN tc.status = 'success' THEN 1 ELSE 0 END,
    t.failed_calls = coalesce(t.failed_calls, 0) + CASE WHEN tc.status IN ['error', 'timeout'] THEN 1 ELSE 0 END,
    t.total_duration_ms = coalesce(t.total_duration_ms, 0) + coalesce(tc.duration_ms, 0),
    t.last_used_at = datetime()
RETURN tc
"""

# Optimized GET_TOOL_STATS using pre-aggregated stats on Tool nodes
# Falls back to computing from ToolCalls for backward compatibility with existing data
GET_TOOL_STATS = """
MATCH (t:Tool)
WITH t,
     coalesce(t.total_calls, 0) AS precomputed_total,
     coalesce(t.successful_calls, 0) AS precomputed_success,
     coalesce(t.total_duration_ms, 0) AS precomputed_duration
// Use precomputed stats if available (non-zero), otherwise compute from tool calls
WITH t, precomputed_total, precomputed_success, precomputed_duration
RETURN t.name AS name,
       t.description AS description,
       precomputed_total AS total_calls,
       CASE WHEN precomputed_total > 0
            THEN toFloat(precomputed_success) / precomputed_total
            ELSE 0.0
       END AS success_rate,
       CASE WHEN precomputed_total > 0
            THEN toFloat(precomputed_duration) / precomputed_total
            ELSE null
       END AS avg_duration
ORDER BY total_calls DESC
"""

# Fallback query that computes stats from ToolCall nodes (for migration/verification)
GET_TOOL_STATS_COMPUTED = """
MATCH (t:Tool)
OPTIONAL MATCH (t)<-[:INSTANCE_OF]-(tc:ToolCall)
WITH t,
     count(tc) AS total_calls,
     sum(CASE WHEN tc.status = 'success' THEN 1 ELSE 0 END) AS successful_calls,
     avg(tc.duration_ms) AS avg_duration
RETURN t.name AS name,
       t.description AS description,
       total_calls,
       CASE WHEN total_calls > 0 THEN toFloat(successful_calls) / total_calls ELSE 0.0 END AS success_rate,
       avg_duration
ORDER BY total_calls DESC
"""

# Migration query to populate pre-aggregated stats from existing ToolCall data
MIGRATE_TOOL_STATS = """
MATCH (t:Tool)
OPTIONAL MATCH (t)<-[:INSTANCE_OF]-(tc:ToolCall)
WITH t,
     count(tc) AS total,
     sum(CASE WHEN tc.status = 'success' THEN 1 ELSE 0 END) AS success,
     sum(CASE WHEN tc.status IN ['error', 'timeout'] THEN 1 ELSE 0 END) AS failed,
     sum(coalesce(tc.duration_ms, 0)) AS duration
SET t.total_calls = total,
    t.successful_calls = success,
    t.failed_calls = failed,
    t.total_duration_ms = duration
RETURN t.name AS name, total AS migrated_calls
"""

SEARCH_TRACES_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('task_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold AND ($success_only = false OR node.success = true)
RETURN node AS rt, score
ORDER BY score DESC
"""


SEARCH_STEPS_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('step_embedding_idx', $limit, $embedding)
YIELD node AS rs, score
WHERE score >= $threshold
MATCH (rt:ReasoningTrace)-[:HAS_STEP]->(rs)
WHERE ($success_only = false OR rt.success = true)
RETURN rs, score, rt.task AS task, rt.outcome AS outcome, rt.success AS trace_success
ORDER BY score DESC
"""

GET_TRACE_WITH_STEPS = """
MATCH (rt:ReasoningTrace {id: $id})
OPTIONAL MATCH (rt)-[:HAS_STEP]->(rs:ReasoningStep)
OPTIONAL MATCH (rs)-[:USES_TOOL]->(tc:ToolCall)
RETURN rt,
       collect(DISTINCT rs) AS steps,
       collect(DISTINCT tc) AS tool_calls
"""

LIST_TRACES = """
MATCH (rt:ReasoningTrace)
WHERE ($session_id IS NULL OR rt.session_id = $session_id)
  AND ($success IS NULL OR rt.success = $success)
  AND ($since IS NULL OR rt.started_at >= datetime($since))
  AND ($until IS NULL OR rt.started_at <= datetime($until))
WITH rt
ORDER BY
    CASE WHEN $order_dir = 'desc' THEN
        CASE WHEN $order_by = 'started_at' THEN rt.started_at
             WHEN $order_by = 'completed_at' THEN rt.completed_at
        END
    END DESC,
    CASE WHEN $order_dir = 'asc' THEN
        CASE WHEN $order_by = 'started_at' THEN rt.started_at
             WHEN $order_by = 'completed_at' THEN rt.completed_at
        END
    END ASC
SKIP $offset LIMIT $limit
RETURN rt
"""

# =============================================================================
# CROSS-MEMORY QUERIES
# =============================================================================

LINK_CONVERSATION_TO_TRACE = """
MATCH (c:Conversation {id: $conversation_id})
MATCH (rt:ReasoningTrace {id: $trace_id})
MERGE (c)-[:HAS_TRACE]->(rt)
RETURN c, rt
"""

LINK_TRACE_TO_MESSAGE = """
MATCH (rt:ReasoningTrace {id: $trace_id})
MATCH (m:Message {id: $message_id})
MERGE (rt)-[:INITIATED_BY]->(m)
RETURN rt, m
"""

LINK_TOOL_CALL_TO_MESSAGE = """
MATCH (tc:ToolCall {id: $tool_call_id})
MATCH (m:Message {id: $message_id})
MERGE (tc)-[:TRIGGERED_BY]->(m)
RETURN tc, m
"""


# Match by id when provided (most specific), else fall back to (name, type),
# else (name) only. The MERGE on the relationship is keyed on the pair so
# re-recording the same touched entity doesn't create duplicate edges.
RECORD_TOUCHED_EDGE_BY_ID = """
MATCH (s:ReasoningStep {id: $step_id})
MATCH (e:Entity {id: $entity_id})
MERGE (s)-[r:TOUCHED]->(e)
ON CREATE SET r.recorded_at = datetime()
RETURN s, e
"""

RECORD_TOUCHED_EDGE_BY_NAME_TYPE = """
MATCH (s:ReasoningStep {id: $step_id})
MERGE (e:Entity {name: $name, type: $type})
ON CREATE SET e.id = coalesce(e.id, $name + ':' + $type),
              e.created_at = datetime()
MERGE (s)-[r:TOUCHED]->(e)
ON CREATE SET r.recorded_at = datetime()
RETURN s, e
"""

RECORD_TOUCHED_EDGE_BY_NAME = """
MATCH (s:ReasoningStep {id: $step_id})
MERGE (e:Entity {name: $name})
ON CREATE SET e.id = coalesce(e.id, $name),
              e.created_at = datetime()
MERGE (s)-[r:TOUCHED]->(e)
ON CREATE SET r.recorded_at = datetime()
RETURN s, e
"""

GET_SESSION_CONTEXT = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
WITH m ORDER BY m.timestamp DESC LIMIT $message_limit
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
WITH collect(DISTINCT m) AS messages, collect(DISTINCT e) AS entities
OPTIONAL MATCH (p:Preference)
WHERE p.created_at > datetime() - duration({days: $preference_days})
RETURN messages, entities, collect(DISTINCT p) AS preferences
"""

# =============================================================================
# UTILITY QUERIES
# =============================================================================

DELETE_SESSION_DATA = """
MATCH (c:Conversation {session_id: $session_id})
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (c)-[:HAS_TRACE]->(rt:ReasoningTrace)
OPTIONAL MATCH (rt)-[:HAS_STEP]->(rs:ReasoningStep)
OPTIONAL MATCH (rs)-[:USES_TOOL]->(tc:ToolCall)
DETACH DELETE c, m, rt, rs, tc
"""

GET_MEMORY_STATS = """
OPTIONAL MATCH (c:Conversation) WITH count(c) AS conversations
OPTIONAL MATCH (m:Message) WITH conversations, count(m) AS messages
OPTIONAL MATCH (e:Entity) WITH conversations, messages, count(e) AS entities
OPTIONAL MATCH (p:Preference) WITH conversations, messages, entities, count(p) AS preferences
OPTIONAL MATCH (f:Fact) WITH conversations, messages, entities, preferences, count(f) AS facts
OPTIONAL MATCH (rt:ReasoningTrace) WITH conversations, messages, entities, preferences, facts, count(rt) AS traces
RETURN conversations, messages, entities, preferences, facts, traces
"""

# =============================================================================
# GRAPH EXPORT QUERIES
# =============================================================================

GET_GRAPH_SHORT_TERM = """
MATCH (c:Conversation)-[r:HAS_MESSAGE]->(m:Message)
WHERE ($session_id IS NULL OR c.session_id = $session_id)
  AND ($since IS NULL OR m.timestamp >= datetime($since))
  AND ($until IS NULL OR m.timestamp <= datetime($until))
WITH c, r, m
LIMIT $limit
RETURN
    collect(DISTINCT {id: c.id, labels: ['Conversation'], properties: properties(c)}) +
    collect(DISTINCT {id: m.id, labels: ['Message'], properties: CASE WHEN $include_embeddings THEN properties(m) ELSE apoc.map.removeKeys(properties(m), ['embedding']) END}) AS nodes,
    collect(DISTINCT {id: elementId(r), type: type(r), from_node: c.id, to_node: m.id, properties: properties(r)}) AS relationships
"""

GET_GRAPH_LONG_TERM = """
MATCH (e:Entity)
WHERE ($since IS NULL OR e.created_at >= datetime($since))
  AND ($until IS NULL OR e.created_at <= datetime($until))
WITH e
LIMIT $limit
OPTIONAL MATCH (e)-[r:RELATED_TO]-(e2:Entity)
OPTIONAL MATCH (p:Preference)
OPTIONAL MATCH (f:Fact)
WITH e, r, e2, collect(DISTINCT p) AS prefs, collect(DISTINCT f) AS facts
RETURN
    collect(DISTINCT {id: e.id, labels: ['Entity'], properties: CASE WHEN $include_embeddings THEN properties(e) ELSE apoc.map.removeKeys(properties(e), ['embedding']) END}) AS nodes,
    collect(DISTINCT {id: elementId(r), type: type(r), from_node: e.id, to_node: e2.id, properties: properties(r)}) AS relationships
"""

GET_GRAPH_REASONING = """
MATCH (rt:ReasoningTrace)
WHERE ($session_id IS NULL OR rt.session_id = $session_id)
  AND ($since IS NULL OR rt.started_at >= datetime($since))
  AND ($until IS NULL OR rt.started_at <= datetime($until))
WITH rt
LIMIT $limit
OPTIONAL MATCH (rt)-[r1:HAS_STEP]->(rs:ReasoningStep)
OPTIONAL MATCH (rs)-[r2:USES_TOOL]->(tc:ToolCall)
RETURN
    collect(DISTINCT {id: rt.id, labels: ['ReasoningTrace'], properties: CASE WHEN $include_embeddings THEN properties(rt) ELSE apoc.map.removeKeys(properties(rt), ['task_embedding']) END}) +
    collect(DISTINCT {id: rs.id, labels: ['ReasoningStep'], properties: CASE WHEN $include_embeddings THEN properties(rs) ELSE apoc.map.removeKeys(properties(rs), ['embedding']) END}) +
    collect(DISTINCT {id: tc.id, labels: ['ToolCall'], properties: properties(tc)}) AS nodes,
    collect(DISTINCT {id: elementId(r1), type: type(r1), from_node: rt.id, to_node: rs.id, properties: properties(r1)}) +
    collect(DISTINCT {id: elementId(r2), type: type(r2), from_node: rs.id, to_node: tc.id, properties: properties(r2)}) AS relationships
"""

GET_GRAPH_ALL = """
MATCH (n)
WHERE ($since IS NULL OR n.created_at >= datetime($since) OR n.timestamp >= datetime($since) OR n.started_at >= datetime($since))
  AND ($until IS NULL OR n.created_at <= datetime($until) OR n.timestamp <= datetime($until) OR n.started_at <= datetime($until))
WITH n
LIMIT $limit
OPTIONAL MATCH (n)-[r]-(m)
RETURN
    collect(DISTINCT {
        id: COALESCE(n.id, elementId(n)),
        labels: labels(n),
        properties: CASE WHEN $include_embeddings THEN properties(n) ELSE apoc.map.removeKeys(properties(n), ['embedding', 'task_embedding']) END
    }) AS nodes,
    collect(DISTINCT {
        id: elementId(r),
        type: type(r),
        from_node: COALESCE(n.id, elementId(n)),
        to_node: COALESCE(m.id, elementId(m)),
        properties: properties(r)
    }) AS relationships
"""

# =============================================================================
# GEOSPATIAL QUERIES
# =============================================================================

UPDATE_ENTITY_LOCATION = """
MATCH (e:Entity {id: $id})
SET e.location = point({latitude: $latitude, longitude: $longitude})
RETURN e
"""

GET_LOCATIONS_WITHOUT_COORDINATES = """
MATCH (e:Entity)
WHERE e.type = 'LOCATION' AND e.location IS NULL
RETURN e.id AS id, e.name AS name, e.subtype AS subtype
ORDER BY e.created_at
"""

SEARCH_LOCATIONS_NEAR = """
MATCH (e:Entity)
WHERE e.type = 'LOCATION'
  AND e.location IS NOT NULL
  AND point.distance(e.location, point({latitude: $latitude, longitude: $longitude})) <= $radius_meters
RETURN e, point.distance(e.location, point({latitude: $latitude, longitude: $longitude})) AS distance_meters
ORDER BY distance_meters
LIMIT $limit
"""

SEARCH_LOCATIONS_IN_BOUNDING_BOX = """
MATCH (e:Entity)
WHERE e.type = 'LOCATION'
  AND e.location IS NOT NULL
  AND point.withinBBox(
      e.location,
      point({latitude: $min_lat, longitude: $min_lon}),
      point({latitude: $max_lat, longitude: $max_lon})
  )
RETURN e
LIMIT $limit
"""

GET_LOCATION_COORDINATES = """
MATCH (e:Entity {id: $id})
WHERE e.location IS NOT NULL
RETURN e.id AS id, e.name AS name, e.location.latitude AS latitude, e.location.longitude AS longitude
"""

# =============================================================================
# PROVENANCE TRACKING QUERIES
# =============================================================================

# Create or update an Extractor node
CREATE_EXTRACTOR = """
MERGE (ex:Extractor {name: $name})
ON CREATE SET
    ex.id = $id,
    ex.version = $version,
    ex.config = $config,
    ex.created_at = datetime()
ON MATCH SET
    ex.version = COALESCE($version, ex.version),
    ex.config = COALESCE($config, ex.config)
RETURN ex
"""

# Link entity to source message with extraction metadata
CREATE_EXTRACTED_FROM_RELATIONSHIP = """
MATCH (e:Entity {id: $entity_id})
MATCH (m:Message {id: $message_id})
MERGE (e)-[r:EXTRACTED_FROM]->(m)
ON CREATE SET
    r.confidence = $confidence,
    r.start_pos = $start_pos,
    r.end_pos = $end_pos,
    r.context = $context,
    r.created_at = datetime()
ON MATCH SET
    r.confidence = CASE WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END
RETURN r
"""

# Link entity to extractor
CREATE_EXTRACTED_BY_RELATIONSHIP = """
MATCH (e:Entity {id: $entity_id})
MATCH (ex:Extractor {name: $extractor_name})
MERGE (e)-[r:EXTRACTED_BY]->(ex)
ON CREATE SET
    r.confidence = $confidence,
    r.extraction_time_ms = $extraction_time_ms,
    r.created_at = datetime()
RETURN r
"""

# Get provenance for an entity
GET_ENTITY_PROVENANCE = """
MATCH (e:Entity {id: $entity_id})
OPTIONAL MATCH (e)-[ef:EXTRACTED_FROM]->(m:Message)
OPTIONAL MATCH (e)-[eb:EXTRACTED_BY]->(ex:Extractor)
RETURN e,
       collect(DISTINCT {message: m, relationship: ef}) AS sources,
       collect(DISTINCT {extractor: ex, relationship: eb}) AS extractors
"""

# Get all entities extracted from a message
GET_ENTITIES_FROM_MESSAGE = """
MATCH (m:Message {id: $message_id})<-[r:EXTRACTED_FROM]-(e:Entity)
RETURN e, r
ORDER BY r.start_pos
"""

# Get all entities extracted by an extractor
GET_ENTITIES_BY_EXTRACTOR = """
MATCH (ex:Extractor {name: $extractor_name})<-[r:EXTRACTED_BY]-(e:Entity)
RETURN e, r
ORDER BY e.created_at DESC
LIMIT $limit
"""

# Get extraction statistics
GET_EXTRACTION_STATS = """
MATCH (e:Entity)
OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(m:Message)
OPTIONAL MATCH (e)-[:EXTRACTED_BY]->(ex:Extractor)
WITH count(DISTINCT e) AS total_entities,
     count(DISTINCT m) AS source_messages,
     collect(DISTINCT ex.name) AS extractors
RETURN total_entities, source_messages, extractors
"""

# Get extractor statistics
GET_EXTRACTOR_STATS = """
MATCH (ex:Extractor)
OPTIONAL MATCH (ex)<-[r:EXTRACTED_BY]-(e:Entity)
RETURN ex.name AS name,
       ex.version AS version,
       count(e) AS entity_count,
       avg(r.confidence) AS avg_confidence
ORDER BY entity_count DESC
"""

# List all extractors
LIST_EXTRACTORS = """
MATCH (ex:Extractor)
OPTIONAL MATCH (ex)<-[:EXTRACTED_BY]-(e:Entity)
RETURN ex, count(e) AS entity_count
ORDER BY entity_count DESC
"""

# Delete provenance for an entity
DELETE_ENTITY_PROVENANCE = """
MATCH (e:Entity {id: $entity_id})
OPTIONAL MATCH (e)-[r1:EXTRACTED_FROM]->()
OPTIONAL MATCH (e)-[r2:EXTRACTED_BY]->()
DELETE r1, r2
RETURN count(r1) + count(r2) AS deleted
"""

# =============================================================================
# ENTITY DEDUPLICATION QUERIES
# =============================================================================

# Find similar entities using embedding similarity
FIND_SIMILAR_ENTITIES_BY_EMBEDDING = """
CALL db.index.vector.queryNodes('entity_embedding_idx', $limit, $embedding)
YIELD node, score
WHERE score >= $threshold AND ($type IS NULL OR node.type = $type)
RETURN node AS e, score
ORDER BY score DESC
"""

# Create SAME_AS relationship for potential duplicates
CREATE_SAME_AS_RELATIONSHIP = """
MATCH (e1:Entity {id: $source_id})
MATCH (e2:Entity {id: $target_id})
WHERE NOT (e1)-[:SAME_AS]-(e2)
CREATE (e1)-[r:SAME_AS {
    confidence: $confidence,
    match_type: $match_type,
    created_at: datetime(),
    status: $status
}]->(e2)
RETURN r
"""

# Get entities that might be duplicates (have SAME_AS relationships)
GET_POTENTIAL_DUPLICATES = """
MATCH (e1:Entity)-[r:SAME_AS]-(e2:Entity)
WHERE r.status = 'pending'
RETURN e1, e2, r
ORDER BY r.confidence DESC
LIMIT $limit
"""

# Get all entities in a SAME_AS cluster
GET_SAME_AS_CLUSTER = """
MATCH (e:Entity {id: $entity_id})
MATCH path = (e)-[:SAME_AS*1..3]-(other:Entity)
RETURN DISTINCT other AS entity, length(path) AS distance
ORDER BY distance
"""

# Merge two entities (mark source as merged into target)
# Note: This query uses CALL subqueries which require Neo4j 4.1+
MERGE_ENTITIES = """
MATCH (source:Entity {id: $source_id})
MATCH (target:Entity {id: $target_id})
// Transfer MENTIONS relationships from source to target using CALL subquery
CALL (source, target) {
    MATCH (source)<-[:MENTIONS]-(m:Message)
    WHERE NOT (m)-[:MENTIONS]->(target)
    MERGE (m)-[:MENTIONS]->(target)
    RETURN count(*) AS mentionsTransferred
}
// Transfer SAME_AS relationships to target using CALL subquery
CALL (source, target) {
    MATCH (source)-[r:SAME_AS]-(other:Entity)
    WHERE other <> target AND NOT (target)-[:SAME_AS]-(other)
    MERGE (target)-[:SAME_AS {
        confidence: r.confidence,
        match_type: 'merged',
        created_at: datetime()
    }]-(other)
    RETURN count(*) AS sameAsTransferred
}
// Mark source as merged
SET source.merged_into = target.id,
    source.merged_at = datetime()
// Add source name as alias on target
SET target.aliases = CASE
    WHEN target.aliases IS NULL THEN [source.name]
    WHEN NOT source.name IN target.aliases THEN target.aliases + source.name
    ELSE target.aliases
END
RETURN source, target
"""

# Get existing entities of a type with embeddings for deduplication
GET_ENTITIES_WITH_EMBEDDINGS = """
MATCH (e:Entity {type: $type})
WHERE e.embedding IS NOT NULL AND e.merged_into IS NULL
RETURN e.id AS id, e.name AS name, e.canonical_name AS canonical_name, e.embedding AS embedding
ORDER BY e.created_at DESC
LIMIT $limit
"""

# Update SAME_AS relationship status
UPDATE_SAME_AS_STATUS = """
MATCH (e1:Entity {id: $source_id})-[r:SAME_AS]-(e2:Entity {id: $target_id})
SET r.status = $status, r.updated_at = datetime()
RETURN r
"""

# Get entity deduplication stats
GET_DEDUPLICATION_STATS = """
MATCH (e:Entity)
OPTIONAL MATCH (e)-[r:SAME_AS]-()
WITH count(DISTINCT e) AS total_entities,
     count(DISTINCT CASE WHEN e.merged_into IS NOT NULL THEN e END) AS merged_entities,
     count(DISTINCT r) AS same_as_relationships
OPTIONAL MATCH ()-[pending:SAME_AS {status: 'pending'}]-()
WITH total_entities, merged_entities, same_as_relationships, count(DISTINCT pending) AS pending_reviews
RETURN total_entities, merged_entities, same_as_relationships, pending_reviews
"""

# =============================================================================
# SCHEMA PERSISTENCE QUERIES
# =============================================================================

CREATE_SCHEMA = """
CREATE (s:Schema {
    id: $id,
    name: $name,
    version: $version,
    description: $description,
    config: $config,
    is_active: $is_active,
    created_at: datetime(),
    created_by: $created_by
})
RETURN s
"""

GET_SCHEMA_BY_NAME = """
MATCH (s:Schema {name: $name})
WHERE s.is_active = true
RETURN s
ORDER BY s.created_at DESC
LIMIT 1
"""

GET_SCHEMA_BY_NAME_VERSION = """
MATCH (s:Schema {name: $name, version: $version})
RETURN s
LIMIT 1
"""

GET_SCHEMA_BY_ID = """
MATCH (s:Schema {id: $id})
RETURN s
"""

LIST_SCHEMAS = """
MATCH (s:Schema)
WHERE $name IS NULL OR s.name = $name
WITH s
ORDER BY s.name, s.created_at DESC
WITH s.name AS name, collect(s) AS versions
RETURN name, head(versions) AS latest, size(versions) AS version_count
"""

LIST_SCHEMA_VERSIONS = """
MATCH (s:Schema {name: $name})
RETURN s
ORDER BY s.created_at DESC
"""

UPDATE_SCHEMA_ACTIVE = """
MATCH (s:Schema {name: $name})
SET s.is_active = false
WITH s
MATCH (active:Schema {id: $id})
SET active.is_active = true
RETURN active
"""

DELETE_SCHEMA = """
MATCH (s:Schema {id: $id})
DELETE s
RETURN count(s) > 0 AS deleted
"""

DELETE_SCHEMA_BY_NAME = """
MATCH (s:Schema {name: $name})
DELETE s
RETURN count(s) AS deleted_count
"""

DEACTIVATE_SCHEMA_VERSIONS = """
MATCH (s:Schema {name: $name})
SET s.is_active = false
RETURN count(s) AS updated
"""

# Schema index creation queries
CREATE_SCHEMA_NAME_INDEX = """
CREATE INDEX schema_name_idx IF NOT EXISTS
FOR (s:Schema)
ON (s.name)
"""

CREATE_SCHEMA_ID_INDEX = """
CREATE INDEX schema_id_idx IF NOT EXISTS
FOR (s:Schema)
ON (s.id)
"""

# =============================================================================
# SCHEMA MANAGEMENT QUERY BUILDERS
# =============================================================================
# These functions generate DDL queries for schema operations.
# They are functions rather than constants because schema element names
# (constraints, indexes) cannot be parameterized in Cypher.


def create_constraint_query(constraint_name: str, label: str, property_name: str) -> str:
    """Generate CREATE CONSTRAINT query for unique property constraint.

    Args:
        constraint_name: Name of the constraint
        label: Node label to apply constraint to
        property_name: Property that must be unique

    Returns:
        Cypher query string
    """
    return f"""
    CREATE CONSTRAINT {constraint_name} IF NOT EXISTS
    FOR (n:{label})
    REQUIRE n.{property_name} IS UNIQUE
    """


def create_index_query(index_name: str, label: str, property_name: str) -> str:
    """Generate CREATE INDEX query for regular property index.

    Args:
        index_name: Name of the index
        label: Node label to index
        property_name: Property to index

    Returns:
        Cypher query string
    """
    return f"""
    CREATE INDEX {index_name} IF NOT EXISTS
    FOR (n:{label})
    ON (n.{property_name})
    """


def create_vector_index_query(
    index_name: str, label: str, property_name: str, dimensions: int
) -> str:
    """Generate CREATE VECTOR INDEX query for vector similarity search.

    Args:
        index_name: Name of the vector index
        label: Node label to index
        property_name: Property containing the vector embedding
        dimensions: Number of dimensions in the vector

    Returns:
        Cypher query string
    """
    return f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR (n:{label})
    ON (n.{property_name})
    OPTIONS {{
        indexConfig: {{
            `vector.dimensions`: {dimensions},
            `vector.similarity_function`: 'cosine'
        }}
    }}
    """


def create_point_index_query(index_name: str, label: str, property_name: str) -> str:
    """Generate CREATE POINT INDEX query for geospatial queries.

    Args:
        index_name: Name of the point index
        label: Node label to index
        property_name: Property containing the point

    Returns:
        Cypher query string
    """
    return f"""
    CREATE POINT INDEX {index_name} IF NOT EXISTS
    FOR (n:{label})
    ON (n.{property_name})
    """


def drop_constraint_query(name: str) -> str:
    """Generate DROP CONSTRAINT query.

    Args:
        name: Name of the constraint to drop

    Returns:
        Cypher query string
    """
    return f"DROP CONSTRAINT {name} IF EXISTS"


def drop_index_query(name: str) -> str:
    """Generate DROP INDEX query.

    Args:
        name: Name of the index to drop

    Returns:
        Cypher query string
    """
    return f"DROP INDEX {name} IF EXISTS"


# Schema introspection queries
SHOW_CONSTRAINTS = "SHOW CONSTRAINTS YIELD name RETURN name"
SHOW_INDEXES = "SHOW INDEXES YIELD name RETURN name"
SHOW_CONSTRAINTS_DETAIL = "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN *"
SHOW_INDEXES_DETAIL = "SHOW INDEXES YIELD name, type, labelsOrTypes, properties RETURN *"

# =============================================================================
# SHORT-TERM MEMORY ENTITY EXTRACTION QUERIES
# =============================================================================

# Get messages without entity links for extraction
GET_MESSAGES_FOR_ENTITY_EXTRACTION = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
WHERE NOT (m)-[:MENTIONS]->(:Entity)
RETURN m.id AS id, m.content AS content
ORDER BY m.timestamp ASC
"""

# Get all messages for a session (for re-extraction)
GET_ALL_MESSAGES_FOR_SESSION = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m.id AS id, m.content AS content
ORDER BY m.timestamp ASC
"""

# Get key entities mentioned in a session (for summaries)
GET_SUMMARY_ENTITIES = """
MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
WITH e.name AS name, e.type AS type, count(*) AS mention_count
ORDER BY mention_count DESC
LIMIT 10
RETURN name, type, mention_count
"""

# Template for vector search with metadata filtering
# Use build_metadata_search_query() to generate the full query
SEARCH_MESSAGES_WITH_METADATA_TEMPLATE = """
CALL db.index.vector.queryNodes('message_embedding_idx', $limit * 2, $embedding)
YIELD node, score
WHERE score >= $threshold
WITH node AS m, score
WHERE m.metadata IS NOT NULL AND {metadata_clause}
RETURN m, score
ORDER BY score DESC
LIMIT $limit
"""


def build_metadata_search_query(metadata_clause: str) -> str:
    """Build vector search query with metadata filtering.

    Args:
        metadata_clause: The WHERE clause for metadata filtering
                        (e.g., "m.metadata CONTAINS 'speaker'")

    Returns:
        Complete Cypher query string for vector search with metadata filter
    """
    return SEARCH_MESSAGES_WITH_METADATA_TEMPLATE.format(metadata_clause=metadata_clause)


# =============================================================================
# EXISTING-GRAPH ADOPTION QUERIES (v0.2)
# =============================================================================
#
# These templates are used by ``SchemaManager.adopt_existing_graph(...)`` to
# attach the ``:Entity`` super-label and the ``id``/``type``/``name``
# properties to nodes in a pre-existing domain graph, so that the library's
# MENTIONS / RELATED_TO writes find them instead of creating duplicates.
#
# The label and the name property are interpolated into the query string
# because Neo4j cannot parameterize identifiers. They are validated against
# a Cypher-safe identifier pattern by the caller before substitution.


def adopt_label_to_entity_query(
    label: str,
    name_property: str,
) -> str:
    """Build a Cypher query that adopts nodes of ``label`` as :Entity.

    Idempotent — already-adopted nodes are skipped by the WHERE clause.
    Returns the count of nodes mutated in this run.
    """
    return f"""
    MATCH (n:`{label}`)
    WHERE NOT n:Entity AND n.`{name_property}` IS NOT NULL
    WITH n,
         CASE
             WHEN n.id IS NOT NULL THEN n.id
             ELSE $label_lc + ':' + toString(n.`{name_property}`)
         END AS computed_id
    SET n:Entity,
        n.type = $type,
        n.id = coalesce(n.id, computed_id),
        n.name = coalesce(n.name, n.`{name_property}`)
    RETURN count(n) AS migrated_count
    """


def count_adoption_skipped_query(label: str, name_property: str) -> str:
    """Count nodes that couldn't be adopted because they lack the name property."""
    return f"""
    MATCH (n:`{label}`)
    WHERE NOT n:Entity AND n.`{name_property}` IS NULL
    RETURN count(n) AS skipped_count
    """


def count_already_adopted_query(label: str) -> str:
    """Count nodes that already carry the :Entity super-label."""
    return f"""
    MATCH (n:`{label}`:Entity)
    RETURN count(n) AS already_adopted_count
    """
