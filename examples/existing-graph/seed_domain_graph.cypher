// Seed a small Movies-style domain graph that pre-dates neo4j-agent-memory.
//
// Notice that none of these nodes carry the :Entity super-label or the
// library's id/type/name properties. After ``adopt.py`` runs, they will
// — and the library's MENTIONS / RELATED_TO writes will link to these
// existing nodes instead of creating duplicates.

MATCH (n) DETACH DELETE n;

CREATE (alice:Person {name: 'Alice Carter', born: 1985})
CREATE (bob:Person   {name: 'Bob Singh',    born: 1979})
CREATE (carol:Person {name: 'Carol Reyes',  born: 1992})

CREATE (matrix:Movie    {title: 'The Matrix',   released: 1999})
CREATE (inception:Movie {title: 'Inception',    released: 2010})
CREATE (arrival:Movie   {title: 'Arrival',      released: 2016})

CREATE (scifi:Genre {name: 'Science Fiction'})
CREATE (drama:Genre {name: 'Drama'})

CREATE (alice)-[:ACTED_IN]->(matrix)
CREATE (bob)-[:DIRECTED]->(inception)
CREATE (carol)-[:ACTED_IN]->(arrival)
CREATE (matrix)-[:IN_GENRE]->(scifi)
CREATE (inception)-[:IN_GENRE]->(scifi)
CREATE (arrival)-[:IN_GENRE]->(scifi)
CREATE (arrival)-[:IN_GENRE]->(drama);
