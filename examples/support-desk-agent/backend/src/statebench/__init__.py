"""STATE-Bench customer support, on top of the memory graph.

- ``vendor/`` is the benchmark's customer-support environment, copied unchanged
  (MIT): the 11 tools, the policy engine and the record schemas.
- :mod:`src.statebench.dataset` reads the 24 tasks under ``data/state-bench``.
- :mod:`src.statebench.world` keeps the environment's records as ontology-typed
  entities in Neo4j and runs the environment's tools against them, writing every
  change back to the graph.
"""
