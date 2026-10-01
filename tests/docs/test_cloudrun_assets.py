"""Check packaging inputs before a costly/remote container deployment."""

import shlex

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10; provided by pytest
    import tomli as tomllib

import pytest


@pytest.mark.docs
def test_packaging_readme_is_copied_into_cloudrun_image(project_root):
    project = tomllib.loads((project_root / "pyproject.toml").read_text())
    readme = project["project"]["readme"]
    readme = readme["file"] if isinstance(readme, dict) else readme
    dockerfile = (project_root / "deploy/cloudrun/Dockerfile").read_text()
    before_install = dockerfile.split("RUN pip install", 1)[0]
    sources = [
        source
        for line in before_install.splitlines()
        if line.startswith("COPY ")
        for source in shlex.split(line)[1:-1]
    ]
    assert readme in sources, f"Packaging needs {readme}; Docker COPY sources are {sources}"
    assert (project_root / readme).is_file()


def _image_command_and_environment(project_root):
    import json
    import re

    source = (project_root / "deploy/cloudrun/Dockerfile").read_text().replace("\\\n", "")
    command = json.loads(re.search(r"^CMD (\[.*?\])", source, re.MULTILINE | re.DOTALL).group(1))
    environment = dict(
        field.split("=", 1)
        for line in source.splitlines()
        if line.startswith("ENV ")
        for field in shlex.split(line[4:])
    )
    return command, environment


@pytest.mark.docs
def test_google_installation_does_not_implicitly_select_google_settings(monkeypatch, tmp_path):
    import os

    from neo4j_agent_memory import MemorySettings
    from neo4j_agent_memory.config.settings import EmbeddingProvider, LLMProvider

    monkeypatch.chdir(tmp_path)
    for name in os.environ:
        if name.startswith(("NAM_", "MEMORY_")):
            monkeypatch.delenv(name)
    defaults = MemorySettings(_env_file=None)
    assert defaults.embedding.provider == EmbeddingProvider.OPENAI
    assert defaults.llm.provider == LLMProvider.OPENAI


@pytest.mark.docs
def test_cloudrun_command_builds_vertex_provider_without_implicit_llm(
    project_root, monkeypatch, tmp_path
):
    import asyncio
    import os
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from click.testing import CliRunner

    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.cli.main import cli
    from neo4j_agent_memory.config.settings import ExtractorType
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder
    from neo4j_agent_memory.llm import factory
    from neo4j_agent_memory.llm.adapters.vertex_ai import VertexAIEmbeddingProvider
    from neo4j_agent_memory.mcp import server

    command, environment = _image_command_and_environment(project_root)
    assert command[0] == "neo4j-agent-memory"
    monkeypatch.chdir(tmp_path)
    for name in os.environ:
        if name.startswith(("NAM_", "MEMORY_")) or name == "OPENAI_API_KEY":
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NEO4J_URI", "bolt://example.invalid:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "fixture-password")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fixture-project")
    # The Dockerfile installs [google]. Emulate availability in the small docs
    # test environment; actual provider/CLI/settings construction runs unchanged.
    package_available = factory._has
    monkeypatch.setattr(
        factory, "_has", lambda package: package == "vertex_ai" or package_available(package)
    )
    captured = {}
    run = AsyncMock()

    def capture(settings, **kwargs):
        captured.update(settings=settings, options=kwargs)
        return SimpleNamespace(run_async=run)

    monkeypatch.setattr(server, "create_mcp_server", capture)
    result = CliRunner().invoke(cli, command[1:])
    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings.backend == "bolt"
    assert settings.extraction.extractor_type is ExtractorType.NONE
    assert settings.llm is None
    assert captured["options"]["auto_preferences"] is False
    assert isinstance(settings.embedding, VertexAIEmbeddingProvider)
    assert settings.embedding.model == "vertex_ai/gemini-embedding-001"
    assert settings.embedding.dimensions == 768
    client = MemoryClient(settings)
    assert client._create_extractor() is None
    embedder = client._create_embedder()
    # Stub only the remote embedding operation. Check the real provider bridge
    # and legacy underlying embedder agree with the configured vector dimension.
    embed = AsyncMock(return_value=[0.1] * 768)
    monkeypatch.setattr(VertexAIEmbedder, "embed", embed)
    assert len(asyncio.run(embedder.embed("synthetic workshop"))) == 768
    assert settings.embedding._ensure_underlying().dimensions == 768
    embed.assert_awaited_once_with("synthetic workshop")
    run.assert_awaited_once_with(transport="http", host="0.0.0.0", port=8080)


@pytest.mark.docs
def test_cloudrun_templates_supply_adc_project_and_document_real_substitutions(project_root):
    import yaml

    build_path = project_root / "deploy/cloudrun/cloudbuild.yaml"
    source = build_path.read_text()
    build = yaml.safe_load(source)
    assert "--config deploy/cloudrun/cloudbuild.yaml ." in source
    assert "_NEO4J_" not in source
    assert set(build["substitutions"]) == {"_REGION", "_SERVICE_NAME", "_REPOSITORY"}
    deploy = next(step for step in build["steps"] if step.get("entrypoint") == "gcloud")
    env_arg = deploy["args"][deploy["args"].index("--set-env-vars") + 1]
    assert env_arg == "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
    service = yaml.safe_load((project_root / "deploy/cloudrun/service.yaml").read_text())
    container = service["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item.get("value") for item in container["env"]}
    assert environment["GOOGLE_CLOUD_PROJECT"] == "PROJECT_ID"
    assert environment["NAM_EXTRACTION__EXTRACTOR_TYPE"] == "none"


@pytest.mark.docs
def test_cloudrun_image_installs_the_vertex_provider_dependency(project_root):
    """The CMD selects vertex_ai/..., which needs the vertexai module inside the image.

    ``test_cloudrun_command_builds_vertex_provider_without_implicit_llm`` emulates that
    module, so this checks the image really installs an extra that provides it.
    """
    import re

    from packaging.requirements import Requirement

    dockerfile = (project_root / "deploy/cloudrun/Dockerfile").read_text().replace("\\\n", "")
    installs = [
        shlex.split(line[len("RUN ") :])
        for line in dockerfile.splitlines()
        if line.startswith("RUN pip install")
    ]
    extras = {
        extra.strip()
        for command in installs
        for argument in command
        for match in [re.fullmatch(r"\.\[(.+)\]", argument)]
        if match
        for extra in match.group(1).split(",")
    }
    assert "mcp" in extras, f"The image serves MCP but installs extras {sorted(extras)}"
    vertex_extras = extras & {"google", "vertex-ai"}
    assert vertex_extras, f"No Vertex AI extra in the image's extras {sorted(extras)}"
    optional = tomllib.loads((project_root / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    for extra in vertex_extras:
        names = {Requirement(requirement).name for requirement in optional[extra]}
        assert "google-cloud-aiplatform" in names, f"[{extra}] no longer provides vertexai"


@pytest.mark.docs
def test_cloudrun_secret_names_and_runtime_identity_agree(project_root):
    """cloudbuild.yaml, service.yaml and the README name the same secrets and identity."""
    import re

    import yaml

    cloudrun = project_root / "deploy/cloudrun"
    build = yaml.safe_load((cloudrun / "cloudbuild.yaml").read_text())
    deploy = next(step for step in build["steps"] if step.get("entrypoint") == "gcloud")
    args = deploy["args"]
    build_secrets = dict(
        pair.split("=", 1) for pair in args[args.index("--set-secrets") + 1].split(",")
    )
    build_secrets = {env: secret.rsplit(":", 1)[0] for env, secret in build_secrets.items()}

    service = yaml.safe_load((cloudrun / "service.yaml").read_text())
    template = service["spec"]["template"]["spec"]
    service_secrets = {
        item["name"]: item["valueFrom"]["secretKeyRef"]["name"]
        for item in template["containers"][0]["env"]
        if "secretKeyRef" in item.get("valueFrom", {})
    }
    assert build_secrets == service_secrets

    readme = (cloudrun / "README.md").read_text()
    loop = re.search(r"for SECRET_NAME in ([^;]+);", readme)
    assert loop, "README no longer grants secret access in a loop"
    assert set(loop.group(1).split()) == set(service_secrets.values())

    def normalise(identity):
        return identity.replace("$PROJECT_ID", "PROJECT_ID")

    build_identity = normalise(args[args.index("--service-account") + 1])
    assert normalise(template["serviceAccountName"]) == build_identity
    readme_identity = re.search(r'RUNTIME_SA="([^"]+)"', readme)
    assert readme_identity and normalise(readme_identity.group(1)) == build_identity
