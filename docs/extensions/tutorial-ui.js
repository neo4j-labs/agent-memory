'use strict'

// Add the small tutorial accessibility layer without replacing upstream templates.
module.exports.register = function () {
  this.on('uiLoaded', ({ uiCatalog }) => {
    const head = uiCatalog.findByType('partial').find((file) => file.path === 'partials/head-scripts.hbs')
    if (!head) throw new Error('The shared UI must supply partials/head-scripts.hbs')
    head.contents = Buffer.concat([head.contents, Buffer.from(`
{{#if (eq page.attributes.role 'code-nocollapse')}}
<link rel="stylesheet" href="{{{uiRootPath}}}/css/tutorial-code.css">
<script defer src="{{{uiRootPath}}}/js/tutorial-code.js"></script>
{{/if}}
`)])
  })
}
