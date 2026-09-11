"""Validation tests for the AWS Financial Services Advisor example.

Repo-level guards only — the example's own 151 tests live in
``examples/financial-services-advisor/aws-financial-services-advisor/backend/tests/``
and run with ``make test`` from that directory. What this file adds is the
handful of properties that are cheap to check from here and expensive to
rediscover: the structure, the library pin, and the two regressions that made
the example look like it worked when it did not.
"""

import ast
import re
from pathlib import Path

import pytest
import tomllib

from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
APP_DIR = EXAMPLES_DIR / "financial-services-advisor" / "aws-financial-services-advisor"
BACKEND_SRC = APP_DIR / "backend" / "src"


class TestAWSFinancialAdvisorStructure:
    """Structure validation for the AWS Financial Services Advisor example."""

    @pytest.fixture
    def app_dir(self):
        """Path to the example."""
        return APP_DIR

    def test_app_directory_exists(self, app_dir):
        """Verify the example directory exists."""
        assert app_dir.exists(), f"Example directory not found: {app_dir}"

    def test_backend_directory_exists(self, app_dir):
        """Verify the backend directory exists."""
        backend = app_dir / "backend"
        assert backend.exists(), f"Backend directory not found: {backend}"

    def test_frontend_directory_exists(self, app_dir):
        """Verify the frontend directory exists."""
        frontend = app_dir / "frontend"
        assert frontend.exists(), f"Frontend directory not found: {frontend}"

    def test_backend_pyproject_exists(self, app_dir):
        """Verify backend pyproject.toml exists."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        assert pyproject.exists(), f"pyproject.toml not found: {pyproject}"

    def test_backend_pyproject_has_neo4j_agent_memory(self, app_dir):
        """Verify backend depends on neo4j-agent-memory."""
        pyproject = app_dir / "backend" / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert "neo4j-agent-memory" in content, "Backend should depend on neo4j-agent-memory"

    def test_backend_pyproject_has_version_pin(self, app_dir):
        """Verify the backend pins a current, capped neo4j-agent-memory range."""
        assert_library_pin(app_dir / "backend" / "pyproject.toml")

    def test_backend_pins_current_strands(self, app_dir):
        """``invoke_async`` / ``stream_async`` and the tool registry need 1.52+.

        The old floor was ``strands-agents>=0.1.0`` — the entire pre-1.0 API.
        """
        data = tomllib.loads((app_dir / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
        strands = next(
            dep for dep in data["project"]["dependencies"] if dep.startswith("strands-agents")
        )
        assert ">=1.52" in strands, f"expected strands-agents>=1.52,<2 — found {strands!r}"
        assert "<2" in strands, f"expected an upper bound on strands-agents — found {strands!r}"

    def test_backend_declares_no_unused_dependencies(self, app_dir):
        """``sse-starlette`` was declared and never imported (the SSE route uses
        Starlette's own ``StreamingResponse``)."""
        data = tomllib.loads((app_dir / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
        declared = {
            dep.split(">")[0].split("[")[0].strip() for dep in data["project"]["dependencies"]
        }
        assert "sse-starlette" not in declared

    def test_license_matches_the_repo(self, app_dir):
        data = tomllib.loads((app_dir / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
        assert "Apache" in str(data["project"]["license"])

    def test_readme_exists(self, app_dir):
        """Verify README.md exists."""
        readme = app_dir / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    def test_backend_has_memory_service(self, app_dir):
        """Verify backend has a memory service module."""
        memory_service = app_dir / "backend" / "src" / "services" / "memory_service.py"
        assert memory_service.exists(), f"memory_service.py not found: {memory_service}"

    def test_backend_has_agents(self, app_dir):
        """Verify backend has agent modules."""
        agents_dir = app_dir / "backend" / "src" / "agents"
        assert agents_dir.exists(), f"Agents directory not found: {agents_dir}"

    def test_infrastructure_declares_its_cdk_entrypoint_deps(self, app_dir):
        """``cdk.json`` runs ``npx ts-node`` and ``bin/app.ts`` imports
        ``source-map-support``; neither was declared, so a fresh checkout could
        not synth."""
        import json

        package = json.loads(
            (app_dir / "infrastructure" / "package.json").read_text(encoding="utf-8")
        )
        dev = package["devDependencies"]
        assert "ts-node" in dev
        assert "source-map-support" in dev
        assert package["scripts"].get("synth"), "expected a credential-free `synth` script"


class TestAWSFinancialAdvisorSyntax:
    """Python syntax validation for AWS Financial Services Advisor."""

    def test_backend_python_files_valid_syntax(self):
        """Verify all backend Python files have valid syntax."""
        if not BACKEND_SRC.exists():
            pytest.skip("Backend src not found")

        for py_file in BACKEND_SRC.rglob("*.py"):
            try:
                ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError as e:
                pytest.fail(f"Syntax error in {py_file}: {e}")


class TestAWSFinancialAdvisorFeatures:
    """The properties that made this example's claims true or false."""

    def test_memory_service_uses_extraction_config(self):
        """Verify memory service uses ExtractionConfig."""
        service = BACKEND_SRC / "services" / "memory_service.py"
        if not service.exists():
            pytest.skip("memory_service.py not found")
        content = service.read_text(encoding="utf-8")
        assert "ExtractionConfig" in content, "memory_service.py should use ExtractionConfig"

    def test_no_simulated_data_in_the_backend(self):
        """The README advertised real Cypher; four agent modules returned
        ``random.choice()`` results from 1,628 lines of dead code."""
        offenders = [
            str(path.relative_to(BACKEND_SRC))
            for path in BACKEND_SRC.rglob("*.py")
            if "random." in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"simulated data is back in {offenders}"

    def test_long_term_memory_is_actually_used(self):
        """Three places advertised a long-term tier that had zero calls."""
        service = (BACKEND_SRC / "services" / "memory_service.py").read_text(encoding="utf-8")
        assert "long_term.add_fact" in service
        assert "long_term.get_facts_about" in service
        assert "long_term.add_preference" in service

    def test_reasoning_traces_record_tool_calls_and_audit_edges(self):
        """A trace with one fabricated step and no tool calls cannot support the
        "full audit trail" claim; ``touched_entities`` is what makes the
        ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` query possible."""
        service = (BACKEND_SRC / "services" / "memory_service.py").read_text(encoding="utf-8")
        assert "reasoning.record_tool_call" in service
        assert "touched_entities" in service
        assert "TraceOutcome" in service
        assert "triggered_by_message_id" in service
        assert ":TOUCHED" in service

    def test_the_agent_is_driven_asynchronously(self):
        """``Agent.__call__`` blocks the event loop, which defeats the SSE route."""
        chat = (BACKEND_SRC / "api" / "routes" / "chat.py").read_text(encoding="utf-8")
        assert "stream_async" in chat
        assert "invoke_async" in chat

    def test_tools_are_registered_with_strands(self):
        """``bind_tool`` must return an ``AgentTool``; a plain function is
        silently dropped by ``ToolRegistry.process_tools``."""
        tools_init = (BACKEND_SRC / "tools" / "__init__.py").read_text(encoding="utf-8")
        assert "return tool(wrapper)" in tools_init

    def test_reads_go_through_the_portable_cypher_accessor(self):
        """``client.graph.execute_read`` is deprecated and bolt-only."""
        service = (BACKEND_SRC / "services" / "neo4j_service.py").read_text(encoding="utf-8")
        assert "query.cypher" in service
        assert "graph.execute_read" not in service

    def test_no_route_reaches_into_a_private_attribute(self):
        routes = BACKEND_SRC / "api" / "routes"
        offenders = [
            str(path.name)
            for path in routes.rglob("*.py")
            if "_graph." in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"routes reaching into Neo4jDomainService internals: {offenders}"

    def test_reports_are_not_kept_in_process_memory(self):
        """Module-level dicts vanish on reload and are per-invocation on Lambda."""
        reports = (BACKEND_SRC / "api" / "routes" / "reports.py").read_text(encoding="utf-8")
        # Word-boundaried so `list_sar_reports` does not match the dict name.
        for name in ("_sar_reports", "_risk_reports"):
            assert not re.search(rf"(?<![A-Za-z0-9_]){name}\b", reports), (
                f"{name} is back: reports must be persisted, not held in the process"
            )
        assert "save_report" in reports
