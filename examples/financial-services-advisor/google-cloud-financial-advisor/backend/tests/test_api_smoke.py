"""FastAPI smoke tests.

These import the real app and call the real route handlers, which is the point:
a handler that reaches for an attribute the object does not have (for example
``MemoryClient._driver``, which never existed) fails here instead of silently
500-ing in the browser.
"""

from __future__ import annotations


def test_app_imports_and_registers_routes(client) -> None:
    paths = set(client.app.openapi()["paths"])
    for expected in (
        "/health",
        "/api/info",
        "/api/chat/stream",
        "/api/graph/stats",
        "/api/graph/memory",
        "/api/traces/{session_id}",
    ):
        assert expected in paths, f"{expected} not registered"


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["platform"] == "Google Cloud"


def test_api_info_lists_agents(client) -> None:
    payload = client.get("/api/info").json()
    names = {agent["name"] for agent in payload["agents"]}
    assert {"supervisor", "kyc_agent", "aml_agent"} <= names


def test_graph_stats_returns_200(client) -> None:
    """Regression test for the routes that used to call ``client._driver``."""
    response = client.get("/api/graph/stats")
    assert response.status_code == 200
    assert response.json()["total_nodes"] == 7


def test_graph_neighbors_returns_200(client) -> None:
    response = client.get("/api/graph/neighbors/CUST-001?depth=2")
    assert response.status_code == 200
    assert response.json()["entity_id"] == "CUST-001"


def test_graph_memory_returns_200(client, fake_neo4j_service) -> None:
    response = client.get("/api/graph/memory?session_id=session-1")
    assert response.status_code == 200
    assert response.json() == {"nodes": [], "relationships": []}
    fake_neo4j_service.get_memory_graph.assert_awaited_once()
    assert fake_neo4j_service.get_memory_graph.await_args.kwargs["session_id"] == "session-1"


def test_graph_query_rejects_writes(client, fake_memory_service) -> None:
    """The read-only guard now lives in the library, not in a local blocklist."""
    fake_memory_service.client.query.cypher.side_effect = ValueError(
        "Only read-only Cypher queries are allowed."
    )
    response = client.post("/api/graph/query", json={"query": "CREATE (n:Foo) RETURN n"})
    assert response.status_code == 400


def test_graph_query_allows_reads(client, fake_memory_service) -> None:
    fake_memory_service.client.query.cypher.return_value = [{"n": {"id": "CUST-001"}}]
    response = client.post(
        "/api/graph/query",
        json={"query": "MATCH (n:Customer) RETURN n LIMIT 1"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_graph_query_allows_a_query_mentioning_dataset(client, fake_memory_service) -> None:
    """The old substring blocklist tripped on any query containing 'dataset'."""
    fake_memory_service.client.query.cypher.return_value = []
    response = client.post(
        "/api/graph/query",
        json={"query": "MATCH (n) WHERE n.dataset = 'sample' RETURN n"},
    )
    assert response.status_code == 200


def test_chat_search_returns_200(client) -> None:
    """Regression test for search_context misreading SearchMemoryResponse."""
    response = client.post("/api/chat/search", json={"query": "structuring"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["results"][0]["type"] == "message"


def test_chat_clear_session(client, fake_memory_service) -> None:
    response = client.delete("/api/chat/session/session-1")
    assert response.status_code == 200
    fake_memory_service.clear_session.assert_awaited_once_with("session-1")


def test_graph_audit_trail_returns_200(client) -> None:
    response = client.get("/api/graph/audit-trail/Global%20Holdings%20Ltd")
    assert response.status_code == 200
    assert response.json()["entity_name"] == "Global Holdings Ltd"


def test_investigation_404_when_absent(client) -> None:
    response = client.get("/api/investigations/INV-NOPE")
    assert response.status_code == 404


def test_investigations_list_reads_neo4j(client, fake_neo4j_service) -> None:
    """Investigations are persisted, not held in a module-level dict."""
    response = client.get("/api/investigations")
    assert response.status_code == 200
    assert response.json() == []
    fake_neo4j_service.list_investigations.assert_awaited_once()


def test_create_investigation_persists_to_neo4j(client, fake_neo4j_service) -> None:
    response = client.post(
        "/api/investigations",
        json={"customer_id": "CUST-001", "reason": "Periodic review"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "INV-TEST0001"
    fake_neo4j_service.create_investigation.assert_awaited_once()


def test_routes_do_not_touch_private_client_internals() -> None:
    """No route may reach into MemoryClient internals that do not exist."""
    from pathlib import Path

    routes_dir = Path(__file__).resolve().parent.parent / "src" / "api" / "routes"
    for module in sorted(routes_dir.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "client._driver" not in source, module.name
        assert "client._database" not in source, module.name


def test_no_module_reaches_into_the_domain_service_internals() -> None:
    """Tool modules must use the public ``read()``, not ``_graph``.

    ``Neo4jDomainService`` used to hold a bare ``Neo4jClient`` as ``_graph``;
    it now holds the ``MemoryClient``, so any surviving ``_graph`` reach-in is
    an ``AttributeError`` waiting to happen at tool-call time.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    for module in sorted(src.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "neo4j_service._graph" not in source, module
        assert "_graph.execute_read" not in source, module
