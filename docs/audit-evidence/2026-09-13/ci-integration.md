# Final CI integration review

Minimal changes made:

- `.github/workflows/ci-python.yml:201`: the integration-test job installs the separate `agent-framework-openai>=1.13,<2` client after `uv sync --group dev --all-extras`.
- The job's readiness probe, documentation contract command and integration suite now use `uv run --no-sync`, preserving the selected extras and supplemental client through all later commands. No later sync occurs in that job.
- `.github/workflows/ci-python.yml:76` and `:80`: Ruff check and format-check include `docs/modules/ROOT/examples` recursively.
- `Makefile:135`: the same directory is included in `RUFF_PATHS` so local lint/format targets match CI.
- `docs/MAINTAINING.md:48`: documents the maintained examples resource, Ruff coverage and exact extra-install/`--no-sync` contract-test sequence; distinguishes offline adapters/providers from live service validation.

Verification:

- PyYAML parsed the workflow. A targeted structure check verified install-before-contract ordering, explicit client constraint, no later resync, and complete maintained-fixture Ruff coverage.
- `bash -n` passed for all lint and integration-test job run blocks.
- The exact `uv run --no-sync pytest tests/docs/test_python_integration_contracts.py -q --timeout=60` test command passed in the previously resolved temporary environment, selected with `UV_PROJECT_ENVIRONMENT` (29 passed). Full output: `logs/agent-memory-ci-framework-contracts.log`.
- Ruff check and format-check passed for all 24 maintained Antora Python files.
- Targeted `git diff --check` passed.

No product-source edits, unrelated workflow job changes, CI dispatch, live provider calls or deployment were performed. These are local checks; the hosted GitHub Actions run itself was not dispatched. The temporary environment initially lacked pytest-timeout, despite its existing declaration in the repository dev group; that initial failure is retained in `logs/agent-memory-ci-framework-contracts-initial.log`, and the declared dependency was installed before repeating the exact command.
