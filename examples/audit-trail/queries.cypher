// Audit queries that exercise the v0.3 reasoning-region polish.
// Run after ``main.py`` has populated the graph.

// ----------------------------------------------------------------------
// Headline audit query — 1-hop, indexed, fast.
// "What did the agent do that touched Anthem?"
// ----------------------------------------------------------------------
MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)
      <-[:HAS_STEP]-(rt:ReasoningTrace)
RETURN rt.task AS task, s.thought AS thought, rt.outcome AS outcome,
       rt.error_kind AS error_kind, rt.success AS success
ORDER BY rt.completed_at DESC;


// ----------------------------------------------------------------------
// Filter by structured outcome — find every trace that timed out.
// Indexed on ReasoningTrace.error_kind.
// ----------------------------------------------------------------------
MATCH (rt:ReasoningTrace {error_kind: 'timeout'})
RETURN rt.task AS task, rt.completed_at AS at, rt.outcome AS summary
ORDER BY at DESC;


// ----------------------------------------------------------------------
// Per-entity reasoning history — every step that touched any :Client.
// ----------------------------------------------------------------------
MATCH (c:Entity {type: 'Client'})<-[t:TOUCHED]-(s:ReasoningStep)
      <-[:HAS_STEP]-(rt:ReasoningTrace)
RETURN c.name AS client,
       rt.task AS task,
       s.thought AS thought,
       t.recorded_at AS recorded_at
ORDER BY recorded_at DESC
LIMIT 20;
