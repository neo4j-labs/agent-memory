"""Standalone smoke checks for the retail assistant backend.

Requires the backend server to be running at http://localhost:8000.
No external dependencies beyond the Python standard library.

Named ``smoke_check.py``, not ``test_backend.py``: the checks below take a
``base_url`` argument and need a live server, so pytest must not collect them
as unit tests.

Usage:
    python smoke_check.py
    python smoke_check.py --base-url http://localhost:9000
    python smoke_check.py --no-chat        # skip the two checks that call the LLM
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:8000"
SESSION_ID = "smoke-test-1"
MAX_OUTPUT = 200


def truncate(obj: object, max_len: int = MAX_OUTPUT) -> str:
    """Return a truncated string representation of obj."""
    text = json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else str(obj)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def request(
    method: str, path: str, base_url: str, body: dict | None = None
) -> tuple[int, dict | str]:
    """Make an HTTP request and return (status_code, parsed_response)."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def check_health(base_url: str) -> bool:
    """The health endpoint reports database connectivity and search mode."""
    status, data = request("GET", "/health", base_url)
    ok = status == 200 and isinstance(data, dict) and data.get("database") == "connected"
    print(f"  {truncate(data)}")
    return ok


def check_chat_sync(base_url: str) -> bool:
    """The synchronous chat endpoint returns a complete answer."""
    body = {"message": "What running shoes do you recommend?", "session_id": SESSION_ID}
    status, data = request("POST", "/chat/sync", base_url, body)
    ok = status == 200 and isinstance(data, dict) and "response" in data
    print(f"  {truncate(data)}")
    return ok


def check_chat_stream(base_url: str) -> bool:
    """The SSE endpoint streams events (reads the first few)."""
    url = f"{base_url}/chat"
    body = json.dumps(
        {"message": "I prefer Nike shoes under $150", "session_id": SESSION_ID}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            events = []
            for line in resp:
                decoded = line.decode().strip()
                if decoded.startswith("data:"):
                    events.append(decoded)
                    if len(events) >= 5:
                        break
            ok = resp.status == 200 and len(events) > 0
            print(f"  events_received={len(events)}")
            for ev in events:
                print(f"  {truncate(ev)}")
            return ok
    except urllib.error.HTTPError as e:
        print(f"  status={e.code}")
        return False


def check_memory_context(base_url: str) -> bool:
    """Memory context returns the three layers."""
    path = f"/memory/context?session_id={SESSION_ID}&query=shoes"
    status, data = request("GET", path, base_url)
    ok = (
        status == 200
        and isinstance(data, dict)
        and {"short_term", "long_term", "reasoning"} <= set(data)
    )
    print(f"  {truncate(data)}")
    return ok


def check_memory_preferences(base_url: str) -> bool:
    """A preference written through the API reads back."""
    status, created = request(
        "POST",
        "/memory/preferences",
        base_url,
        {"category": "brand", "preference": "Nike", "session_id": SESSION_ID},
    )
    if status != 201:
        print(f"  POST /memory/preferences -> {status}: {truncate(created)}")
        return False

    status, data = request("GET", f"/memory/preferences?session_id={SESSION_ID}", base_url)
    ok = status == 200 and isinstance(data, dict) and bool(data.get("preferences"))
    print(f"  {truncate(data)}")
    return ok


def check_memory_graph(base_url: str) -> bool:
    """The session graph returns nodes and edges."""
    path = f"/memory/graph?session_id={SESSION_ID}"
    status, data = request("GET", path, base_url)
    ok = status == 200 and isinstance(data, dict) and "nodes" in data and "edges" in data
    print(f"  nodes={len(data['nodes']) if isinstance(data, dict) else '?'}")
    return ok


def check_memory_duplicates(base_url: str) -> bool:
    """The dedup review queue answers with candidates and stats."""
    status, data = request("GET", "/memory/duplicates", base_url)
    ok = status == 200 and isinstance(data, dict) and "duplicates" in data and "stats" in data
    print(f"  {truncate(data)}")
    return ok


def check_product_search(base_url: str) -> bool:
    """Product search returns rows and says which branch ran."""
    status, data = request("GET", "/products/search?query=shoe", base_url)
    ok = (
        status == 200
        and isinstance(data, dict)
        and "products" in data
        and "total" in data
        and data.get("search_mode") in {"vector", "text"}
    )
    print(f"  total={data.get('total')} mode={data.get('search_mode')}")
    return ok


def check_categories(base_url: str) -> bool:
    """Category listing is populated by the sample loader."""
    status, data = request("GET", "/products/categories", base_url)
    ok = status == 200 and isinstance(data, dict) and "categories" in data
    print(f"  {truncate(data)}")
    return ok


def _get_a_product_id(base_url: str) -> str | None:
    """Helper to fetch a product ID from search results."""
    status, data = request("GET", "/products/search?query=shoe", base_url)
    if status == 200 and isinstance(data, dict) and data.get("products"):
        return data["products"][0]["id"]
    return None


def check_get_product(base_url: str) -> bool:
    """Product detail lookup works for an id from search."""
    product_id = _get_a_product_id(base_url)
    if not product_id:
        print("  SKIP: no products found — run `python -m data.load_products` first")
        return True

    status, data = request("GET", f"/products/{product_id}", base_url)
    ok = status == 200 and isinstance(data, dict) and "name" in data
    print(f"  {truncate(data)}")
    return ok


def check_related_products(base_url: str) -> bool:
    """Related products works with and without a relationship filter."""
    product_id = _get_a_product_id(base_url)
    if not product_id:
        print("  SKIP: no products found — run `python -m data.load_products` first")
        return True

    status, data = request("GET", f"/products/{product_id}/related", base_url)
    ok = status == 200 and isinstance(data, dict) and "related_products" in data
    print(f"  related={len(data.get('related_products', [])) if isinstance(data, dict) else '?'}")

    status, filtered = request(
        "GET", f"/products/{product_id}/related?relationship_type=similar", base_url
    )
    ok = ok and status == 200
    print(f"  relationship_type=similar -> {status}")
    return ok


def check_relationship_type_is_an_allow_list(base_url: str) -> bool:
    """An injected relationship type is rejected by validation, not executed."""
    product_id = _get_a_product_id(base_url) or "nike-pegasus-40"
    injected = "INJECT%5D-%3E()%3C-%5B:x"  # INJECT]->()<-[:x
    status, _ = request(
        "GET", f"/products/{product_id}/related?relationship_type={injected}", base_url
    )
    print(f"  status={status} (expected 422)")
    return status == 422


CHECKS = [
    ("Health check", check_health, False),
    ("Sync chat", check_chat_sync, True),
    ("Streaming chat (SSE)", check_chat_stream, True),
    ("Memory context", check_memory_context, False),
    ("Memory preferences (write + read)", check_memory_preferences, False),
    ("Memory graph", check_memory_graph, False),
    ("Duplicate review queue", check_memory_duplicates, False),
    ("Product search", check_product_search, False),
    ("Categories", check_categories, False),
    ("Get product", check_get_product, False),
    ("Related products", check_related_products, False),
    ("Relationship type allow-list (422)", check_relationship_type_is_an_allow_list, False),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke checks for the retail assistant backend")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--no-chat",
        action="store_true",
        help="Skip the two checks that call the LLM (no OPENAI_API_KEY needed)",
    )
    args = parser.parse_args()

    checks = [(name, fn) for name, fn, needs_llm in CHECKS if not (needs_llm and args.no_chat)]

    print(f"Testing backend at {args.base_url}\n")

    passed = 0
    failed = 0

    for name, fn in checks:
        print(f"[CHECK] {name}")
        try:
            if fn(args.base_url):
                print("  PASS\n")
                passed += 1
            else:
                print("  FAIL\n")
                failed += 1
        except Exception as e:
            print(f"  ERROR: {e}\n")
            failed += 1

    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
