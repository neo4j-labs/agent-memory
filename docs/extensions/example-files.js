'use strict'

// Expose maintained standalone programs to on-page example$ includes without
// copying their source or publishing downloadable attachments.
const { readFileSync } = require('node:fs')
const path = require('node:path')

const ROOT = path.resolve(__dirname, '../..')
const EXAMPLE_FILES = {
  'no_llm/main.py': 'examples/no_llm/main.py',
  'eval-harness/main.py': 'examples/eval-harness/main.py',
  'eval-harness/ci_gate.py': 'examples/eval-harness/ci_gate.py',
  'team-memory/_shared.py': 'examples/claude-code-team-memory/_shared.py',
  'team-memory/seed_workspace.py': 'examples/claude-code-team-memory/seed_workspace.py',
  'team-memory/doctor.py': 'examples/claude-code-team-memory/doctor.py',
  'team-memory/.mcp.json.example': 'examples/claude-code-team-memory/.mcp.json.example',
  'team-memory/claude_desktop_config.json.example': 'examples/claude-code-team-memory/claude_desktop_config.json.example',
  'team-memory/cursor_mcp.json.example': 'examples/claude-code-team-memory/cursor_mcp.json.example',
}

module.exports.register = function () {
  this.on('contentClassified', ({ contentCatalog }) => {
    const component = contentCatalog.getComponent('agent-memory')
    if (!component) return
    for (const { version } of component.versions) {
      for (const [relative, source] of Object.entries(EXAMPLE_FILES)) {
        contentCatalog.addFile({
          contents: readFileSync(path.join(ROOT, source)),
          src: { component: component.name, version, module: 'ROOT', family: 'example', relative },
        })
      }
    }
  })
}

module.exports.EXAMPLE_FILES = EXAMPLE_FILES
