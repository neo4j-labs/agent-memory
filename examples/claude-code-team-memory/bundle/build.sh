#!/usr/bin/env bash
# Package the self-hosted MCP server as a double-clickable Claude Desktop
# extension (.mcpb).
#
# The manifest is the one maintained in the repository at
# deploy/mcpb/manifest.json — this script does not define a second copy, it
# stages that one plus its README and zips the pair. Anyone who changes the
# server's command line changes it in one place.
#
# Usage:
#   ./bundle/build.sh              # writes bundle/dist/neo4j-agent-memory.mcpb
#   OUT_DIR=/tmp ./bundle/build.sh
#
# Then: Claude Desktop → Settings → Extensions → Install from file.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${here}/../../.." && pwd)"
manifest="${repo_root}/deploy/mcpb/manifest.json"
readme="${repo_root}/deploy/mcpb/README.md"
out_dir="${OUT_DIR:-${here}/dist}"
out_file="${out_dir}/neo4j-agent-memory.mcpb"

if [[ ! -f "${manifest}" ]]; then
  echo "error: manifest not found at ${manifest}" >&2
  echo "       run this from a checkout of neo4j-labs/agent-memory" >&2
  exit 1
fi

# Fail before zipping rather than shipping a bundle Claude Desktop rejects.
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import json,sys; json.load(open(sys.argv[1]))" "${manifest}"
fi

echo "manifest : ${manifest}"
echo "server   : $(python3 -c "
import json, sys
m = json.load(open(sys.argv[1]))
s = m['server']
print(s['command'], ' '.join(s['args']))
" "${manifest}" 2>/dev/null || echo '(python3 unavailable — not inspected)')"

staging="$(mktemp -d)"
trap 'rm -rf "${staging}"' EXIT
cp "${manifest}" "${staging}/manifest.json"
[[ -f "${readme}" ]] && cp "${readme}" "${staging}/README.md"

mkdir -p "${out_dir}"
rm -f "${out_file}"
(cd "${staging}" && zip -q -r "${out_file}" .)

echo
echo "built    : ${out_file}"
echo
echo "The bundle starts the same server as the 'team-memory-self-hosted' entry in"
echo "claude_desktop_config.json.example — the manifest uses the short uvx spec"
echo "form ('uvx neo4j-agent-memory[mcp] mcp serve') and takes the default"
echo "profile, where the config file spells out --from, --profile and"
echo "--session-strategy. Either way NEO4J_PASSWORD (bolt) or MEMORY_API_KEY"
echo "(hosted NAMS) still has to be supplied; Claude Desktop prompts for the"
echo "manifest's env_required values rather than storing them in JSON."
