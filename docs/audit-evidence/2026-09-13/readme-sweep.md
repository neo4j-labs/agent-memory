# Final README entrypoint sweep

Scope: 48 repository README variants, enumerated by `rg --files` excluding OpenWiki, node_modules, virtual environments and build/dist directories. Exact inventory: `readme-inventory.txt`. This was a bounded command/runtime/scope/release-claim rescan, not a fresh execution of every example or a full line-by-line technical audit of all 48 files.

## Frontend runtime corrections

Read each frontend's own package.json, package-lock.json and README for:

- examples/lennys-memory
- examples/full-stack-chat-agent
- examples/microsoft_agent_retail_assistant
- examples/financial-services-advisor/google-cloud-financial-advisor
- examples/financial-services-advisor/aws-financial-services-advisor

Four frontend root manifests/lock entries declare Node >=22. Microsoft retail has no root engine declaration; its Next 16.3.5 requires >=20.9 and its tests use Node's type-stripping flags. All five current locked development toolchains have dependencies requiring Node >=22.13 on the 22 line (ESLint visitor/core packages; Google also jsdom). Vitest 5 additionally excludes odd Node 25 in applicable projects.

Changed the parent and frontend README prerequisites (10 files) to **Node 22.13+ on the 22 release line, or Node 24, for development/checks**, explicitly independent of the TypeScript memory SDK. No package manifests, lockfiles, dependency versions or Docker images changed.

Static semver check using npm's existing semver library evaluated every platform-applicable lock entry for this host (darwin/arm64), confirming all five accept 22.13.0 and 24.0.0 and reject 22.12.0 because of named tooling. Report: `frontend-engines.json`. Other-platform optional binaries were excluded from that host check; these applications were not built or launched in this sweep.

The npm script names shown in these ten current parent/frontend READMEs were compared against the corresponding frontend manifest scripts: no unmatched names outside historical verification notes.

## Current source versus historical verification

Per root instruction, retained 28 existing 2026-09-10 verification footers as explicitly labeled **Historical verification report** blocks, with links to `DOCUMENTATION_REMEDIATION_STATUS.md` for current source/artifact evidence. Exact changed list: `historical-readme-paths.txt`.

This preserves their reported environments, previous passing counts, mocked/live descriptions and historical development/release statements without presenting them as newly verified current artifact compatibility. No changelog, design document or prior audit was changed. All 28 relative links resolve. No unqualified top-level `_Verified against` or `**Verified against` footer remains among the 48 scanned README variants.

Also corrected the Microsoft retail current troubleshooting instruction that broad `neo4j-agent-memory>=0.5.0` establishes GA-interface availability. It now points to this checkout's backend setup and the current artifact evidence. Updated examples gallery contribution guidance to record exact source commit, installed artifacts, checks, date and mocked/live boundaries. Removed Lenny's unsupported production-grade label.

## TypeScript README claim cleanup

- The eve gallery row and opening description no longer promise surviving hosted shopper profiles or successful order storage. They identify the mocked profile/order contract and unsupported transport operations. The local interaction is explicitly an intended contract, not a passing hosted persistence check.
- TypeScript examples gallery and Vercel example identify current source rather than implying the selected npm artifact shipped every shown integration shape.
- Provider README key acquisition no longer asserts current service pricing (`free`).

No stale `docs-watch`, `docs-dev`, `npm run docs`, or other obvious docs-build commands surfaced in the README pattern rescan. Parent owns broader site and publication command checks.

Validation: scoped `git diff --check` passes; 28 historical-note links resolve; frontend script name comparison passes; static lock engine evidence recorded. No new tests were added for these reversible prose/metadata corrections and no dependency or live-service action was performed.
