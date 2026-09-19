'use strict'

// Expose maintained standalone programs to on-page example$ includes without
// copying their source or publishing downloadable attachments.
const { readFileSync } = require('node:fs')
const path = require('node:path')

const ROOT = path.resolve(__dirname, '../..')
const EXAMPLE_FILES = require('./example-files.json')

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
