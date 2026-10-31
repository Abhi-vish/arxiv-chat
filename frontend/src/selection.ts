// Must match MAX_SELECTION_CHARS in backend/src/schemas.py
export const MAX_SELECTION_CHARS = 4000

export interface Selection {
  text: string
  truncated: boolean
}

export function clipSelection(text: string): Selection {
  return text.length > MAX_SELECTION_CHARS
    ? { text: text.slice(0, MAX_SELECTION_CHARS), truncated: true }
    : { text, truncated: false }
}

/**
 * Highlighted range -> plain text. KaTeX renders each formula twice (visual +
 * hidden MathML), so a naive toString() returns garbled duplicates. We swap each
 * formula for its original LaTeX source before reading the text.
 */
export function selectionToText(range: Range): string {
  const frag = range.cloneContents()

  frag.querySelectorAll('.katex').forEach((el) => {
    const tex = el
      .querySelector('annotation[encoding="application/x-tex"]')
      ?.textContent?.trim()
    const display = !!el.closest('.katex-display')
    el.replaceWith(document.createTextNode(tex ? (display ? `$$${tex}$$` : `$${tex}$`) : ''))
  })

  // innerText needs layout to produce line breaks between blocks,
  // so measure in a hidden, off-screen box.
  const box = document.createElement('div')
  box.style.cssText = 'position:fixed;left:-99999px;top:0;width:600px'
  box.appendChild(frag)
  document.body.appendChild(box)
  const text = box.innerText
  box.remove()

  return text.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}