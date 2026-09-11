// A small Movies-style domain graph that pre-dates neo4j-agent-memory.
//
// Stand-in for "the Neo4j graph you already have in production":
//   * none of these nodes carry the :Entity super-label,
//   * :Movie uses `title` (not `name`) for its display name,
//   * :Person and :Movie already have application-assigned UUID `id`
//     properties; :Genre has none, so adoption has to generate one.
//
// After ``adopt.py`` runs, every node here carries :Entity plus the
// library's id/type/name properties, and the library's MENTIONS /
// RELATED_TO writes link to these nodes instead of creating duplicates.
//
// Every statement is a MERGE keyed on a stable identifier, so running the
// seed twice is a no-op. ``seed.py`` executes this file; it never runs an
// unscoped delete. See ``seed.py --reset`` for the scoped, opt-in reset.

MERGE (p:Person {id: 'b0f2a1c4-0001-4a1e-9f01-0000000000a1'})
  SET p.name = 'Alice Carter', p.born = 1985;

MERGE (p:Person {id: 'b0f2a1c4-0002-4a1e-9f01-0000000000a2'})
  SET p.name = 'Bob Singh', p.born = 1979;

MERGE (p:Person {id: 'b0f2a1c4-0003-4a1e-9f01-0000000000a3'})
  SET p.name = 'Carol Reyes', p.born = 1992;

MERGE (m:Movie {id: 'c1e3b2d5-0001-4b2f-8a02-0000000000b1'})
  SET m.title = 'The Matrix', m.released = 1999;

MERGE (m:Movie {id: 'c1e3b2d5-0002-4b2f-8a02-0000000000b2'})
  SET m.title = 'Inception', m.released = 2010;

MERGE (m:Movie {id: 'c1e3b2d5-0003-4b2f-8a02-0000000000b3'})
  SET m.title = 'Arrival', m.released = 2016;

// No `id` property here on purpose: adoption generates a deterministic
// `genre:<name>` id for nodes that do not already have one.
MERGE (g:Genre {name: 'Science Fiction'});
MERGE (g:Genre {name: 'Drama'});

MATCH (p:Person {name: 'Alice Carter'}), (m:Movie {title: 'The Matrix'})
MERGE (p)-[:ACTED_IN]->(m);

MATCH (p:Person {name: 'Bob Singh'}), (m:Movie {title: 'Inception'})
MERGE (p)-[:DIRECTED]->(m);

MATCH (p:Person {name: 'Carol Reyes'}), (m:Movie {title: 'Arrival'})
MERGE (p)-[:ACTED_IN]->(m);

MATCH (m:Movie {title: 'The Matrix'}), (g:Genre {name: 'Science Fiction'})
MERGE (m)-[:IN_GENRE]->(g);

MATCH (m:Movie {title: 'Inception'}), (g:Genre {name: 'Science Fiction'})
MERGE (m)-[:IN_GENRE]->(g);

MATCH (m:Movie {title: 'Arrival'}), (g:Genre {name: 'Science Fiction'})
MERGE (m)-[:IN_GENRE]->(g);

MATCH (m:Movie {title: 'Arrival'}), (g:Genre {name: 'Drama'})
MERGE (m)-[:IN_GENRE]->(g);
