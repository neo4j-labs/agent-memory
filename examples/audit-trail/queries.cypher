// Audit queries over the reasoning audit edges (v0.2.0+).
// Run after ``main.py`` has populated the graph. Bolt only — ``:TOUCHED``
// edges are a bolt-side schema feature.

// ----------------------------------------------------------------------
// Headline audit query — 1-hop, indexed, fast.
// "What did the agent do that touched Anthem, and who asked for it?"
// ----------------------------------------------------------------------
MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)
      <-[:HAS_STEP]-(rt:ReasoningTrace)
OPTIONAL MATCH (rt)-[:INITIATED_BY]->(m:Message)
RETURN rt.task AS task, s.thought AS thought, rt.outcome AS summary,
       rt.success AS success, rt.error_kind AS error_kind,
       rt.metrics_json AS metrics, m.content AS triggered_by
ORDER BY rt.completed_at DESC;


// ----------------------------------------------------------------------
// Filter by structured outcome — find every trace that timed out.
// Indexed on ReasoningTrace.error_kind.
// ----------------------------------------------------------------------
MATCH (rt:ReasoningTrace {error_kind: 'timeout'})
RETURN rt.task AS task, rt.completed_at AS at, rt.outcome AS summary
ORDER BY at DESC;


// ----------------------------------------------------------------------
// Per-entity reasoning history — every step that touched any CLIENT.
// Entity types are stored as uppercase strings.
// ----------------------------------------------------------------------
MATCH (c:Entity {type: 'CLIENT'})<-[t:TOUCHED]-(s:ReasoningStep)
      <-[:HAS_STEP]-(rt:ReasoningTrace)
RETURN c.name AS client,
       rt.task AS task,
       s.thought AS thought,
       t.recorded_at AS recorded_at
ORDER BY recorded_at DESC
LIMIT 20;
