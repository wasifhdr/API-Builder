import { useCallback, useEffect, useState } from 'react'

// A Document Picture-in-Picture window starts as a blank same-origin document —
// clone the app's stylesheets into it so portaled components keep their styling.
// Idempotent: marks the window so adopting it later (see usePipWindow) doesn't
// duplicate every rule.
function copyStyles(pip: Window & { __stylesCopied?: boolean }) {
  if (pip.__stylesCopied) return
  pip.__stylesCopied = true
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
 * Opens a Document Picture-in-Picture window and styles it. Must be called from
 * a user gesture (transient activation) — the API rejects otherwise. Exported so
 * a page can open the floating controls inside the same click that starts the
 * recorder (before the recorder's browser steals focus); `usePipWindow` then
 * adopts that window on the next page. Returns null when unsupported.
 */
export async function openPipWindow(width: number, height: number): Promise<Window | null> {
  const api = window.documentPictureInPicture
  if (!api) return null
  const pip = await api.requestWindow({ width, height })
  copyStyles(pip)
  return pip
}

/**
 * Manages a Document Picture-in-Picture window — an always-on-top floating
 * window (above every app, including the Playwright recorder browser) that
 * React can portal into. Chromium-only; check `supported` before offering it.
 *
 * On mount it adopts any window already opened via `openPipWindow` (e.g. during
 * the "Start recording" click on the previous page), so the controls are
 * already floating when this page loads.
 */
export function usePipWindow() {
  const [pipWindow, setPipWindow] = useState<Window | null>(null)
  const supported = 'documentPictureInPicture' in window

  const adopt = useCallback((pip: Window) => {
    copyStyles(pip)
    pip.addEventListener('pagehide', () => setPipWindow(null))
    setPipWindow(pip)
  }, [])

  // Must be called from a user gesture (e.g. a click handler).
  const open = useCallback(
    async (width: number, height: number) => {
      const pip = await openPipWindow(width, height)
      if (pip) adopt(pip)
    },
    [adopt],
  )

  const close = useCallback(() => {
    setPipWindow((current) => {
      current?.close()
      return null
    })
  }, [])

  // Adopt a window opened before this component mounted, on the previous page.
  useEffect(() => {
    const existing = window.documentPictureInPicture?.window
    if (existing && !existing.closed) adopt(existing)
  }, [adopt])

  // Don't leave an orphaned floating window behind on unmount/navigation.
  useEffect(() => () => pipWindow?.close(), [pipWindow])

  return { supported, pipWindow, open, close }
}
