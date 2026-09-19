/* Native keyboard controls using the official UI's shell-command normalization. */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.body.classList.contains('code-nocollapse')) return
  const article = document.querySelector('article.doc')
  if (!article) return
  const status = document.createElement('p')
  status.className = 'show-for-sr'
  status.setAttribute('role', 'status')
  status.setAttribute('aria-live', 'polite')
  article.appendChild(status)

  article.querySelectorAll('.listingblock').forEach((listing, index) => {
    const source = listing.querySelector('pre')
    if (!source) return
    const title = listing.querySelector('.title')?.textContent.trim()
    const label = title?.replace(/^Save as\s+/, '') || `code block ${index + 1}`
    source.tabIndex = 0
    source.setAttribute('role', 'region')
    source.setAttribute('aria-label', `${label} source`)

    const original = listing.querySelector('span.btn-copy')
    if (!original) return // A future upstream button already handles its own keyboard events.
    const button = document.createElement('button')
    button.type = 'button'
    button.className = original.className
    button.setAttribute('aria-label', `Copy code: ${label}`)
    button.title = `Copy code: ${label}`
    button.addEventListener('click', async () => {
      const code = source.querySelector('code')
      const language = code.className.match(/language-([\w-]+)/)?.[1]
      let text = code.innerText
      if (['bash', 'sh', 'shell', 'console'].includes(language)) {
        text = window.neo4jDocs.copyableCommand(text)
      }
      text = text.replace(/[ \t]+\n/g, '\n').trimEnd()
      let copied = false
      try {
        await navigator.clipboard.writeText(text)
        copied = true
      } catch (_) {
        // Retain a fallback for clients without the asynchronous Clipboard API.
        const temporary = document.createElement('textarea')
        temporary.value = text
        temporary.setAttribute('readonly', '')
        temporary.className = 'show-for-sr'
        document.body.appendChild(temporary)
        temporary.select()
        try { copied = document.execCommand('copy') } catch (_) { copied = false } finally { temporary.remove() }
      }
      button.focus({ preventScroll: true })
      status.textContent = copied ? `Copied ${label}.` : `Copy unavailable for ${label}. Select its source and use your browser's Copy command.`
      const feedback = listing.querySelector('.copy-success')
      if (feedback) {
        feedback.textContent = copied ? 'Copied!' : 'Copy unavailable'
        feedback.classList.remove('hidden')
        setTimeout(() => feedback.classList.add('hidden'), 2000)
      }
    })
    original.replaceWith(button)
  })
})
