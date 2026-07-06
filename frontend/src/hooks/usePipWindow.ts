import { useCallback, useEffect, useState } from 'react'

// A Document Picture-in-Picture window starts as a blank same-origin document —
// clone the app's stylesheets into it so portaled components keep their styling.
function copyStyles(pip: Window) {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const style = pip.document.createElement('style')
      style.textContent = Array.from(sheet.cssRules)
        .map((rule) => rule.cssText)
        .join('\n')
      pip.document.head.appendChild(style)
    } catch {
      // Cross-origin sheets throw on cssRules access — link them instead.
      if (sheet.href) {
        const link = pip.document.createElement('link')
        link.rel = 'stylesheet'
        link.href = sheet.href
        pip.document.head.appendChild(link)
      }
    }
  }
}

/**
 * Manages a Document Picture-in-Picture window — an always-on-top floating
 * window (above every app, including the Playwright recorder browser) that
 * React can portal into. Chromium-only; check `supported` before offering it.
 */
export function usePipWindow() {
  const [pipWindow, setPipWindow] = useState<Window | null>(null)
  const supported = 'documentPictureInPicture' in window

  // Must be called from a user gesture (e.g. a click handler).
  const open = useCallback(async (width: number, height: number) => {
    const api = window.documentPictureInPicture
    if (!api) return
    const pip = await api.requestWindow({ width, height })
    copyStyles(pip)
    pip.addEventListener('pagehide', () => setPipWindow(null))
    setPipWindow(pip)
  }, [])

  const close = useCallback(() => {
    setPipWindow((current) => {
      current?.close()
      return null
    })
  }, [])

  // Don't leave an orphaned floating window behind on unmount/navigation.
  useEffect(() => () => pipWindow?.close(), [pipWindow])

  return { supported, pipWindow, open, close }
}
