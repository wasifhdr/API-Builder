// Minimal typings for the Document Picture-in-Picture API (Chromium 116+),
// which TypeScript's DOM lib doesn't ship yet.
// https://developer.mozilla.org/en-US/docs/Web/API/Document_Picture-in-Picture_API

interface DocumentPictureInPictureOptions {
  width?: number
  height?: number
  disallowReturnToOpener?: boolean
}

interface DocumentPictureInPicture extends EventTarget {
  readonly window: Window | null
  requestWindow(options?: DocumentPictureInPictureOptions): Promise<Window>
}

interface Window {
  readonly documentPictureInPicture?: DocumentPictureInPicture
}
