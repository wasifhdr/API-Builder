import { useCallback, useEffect, useRef, useState } from 'react'

/** Speech-to-text over the browser's built-in Web Speech API (Chrome/Edge).
 * No extra dependency and no backend hop — the recognizer runs in the page, so
 * prompt audio never touches our server. Firefox has no implementation, hence
 * the `supported` flag callers use to hide the control entirely. */

interface SpeechRecognitionAlternative {
  transcript: string
}

interface SpeechRecognitionResult {
  isFinal: boolean
  0: SpeechRecognitionAlternative
}

interface SpeechRecognitionEvent {
  resultIndex: number
  results: { length: number; [index: number]: SpeechRecognitionResult }
}

interface SpeechRecognitionErrorEvent {
  error: string
}

interface SpeechRecognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
}

type SpeechRecognitionCtor = new () => SpeechRecognition

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const ERROR_MESSAGE: Record<string, string> = {
  'not-allowed': 'Microphone access was blocked. Allow it in the browser and try again.',
  'service-not-allowed': 'Microphone access was blocked. Allow it in the browser and try again.',
  'audio-capture': 'No microphone found.',
  network: 'Speech recognition lost its network connection.',
}

interface Options {
  /** Called with each finalized phrase, ready to append to the field. */
  onFinal: (text: string) => void
  lang?: string
}

export function useDictation({ onFinal, lang = 'en-US' }: Options) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)

  const recognitionRef = useRef<SpeechRecognition | null>(null)
  // Chrome ends the session on its own after a pause in speech. This tracks
  // whether that end was the user's doing, so an unsolicited stop can be
  // restarted instead of silently dropping the mic mid-sentence.
  const wantListeningRef = useRef(false)
  const onFinalRef = useRef(onFinal)
  onFinalRef.current = onFinal

  const [supported] = useState(() => typeof window !== 'undefined' && getRecognitionCtor() !== null)

  const stop = useCallback(() => {
    wantListeningRef.current = false
    recognitionRef.current?.stop()
    setListening(false)
    setInterim('')
  }, [])

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor()
    if (!Ctor || wantListeningRef.current) return

    const recognition = new Ctor()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = lang

    recognition.onresult = (event) => {
      let pending = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (result.isFinal) {
          const text = result[0].transcript.trim()
          if (text) onFinalRef.current(text)
        } else {
          pending += result[0].transcript
        }
      }
      setInterim(pending)
    }

    recognition.onerror = (event) => {
      // Silence between phrases surfaces as an error; it is not one worth showing.
      if (event.error === 'no-speech' || event.error === 'aborted') return
      setError(ERROR_MESSAGE[event.error] ?? 'Could not capture audio.')
      wantListeningRef.current = false
      setListening(false)
      setInterim('')
    }

    recognition.onend = () => {
      setInterim('')
      if (wantListeningRef.current) {
        recognition.start()
      } else {
        setListening(false)
      }
    }

    recognitionRef.current = recognition
    wantListeningRef.current = true
    setError(null)
    try {
      recognition.start()
      setListening(true)
    } catch {
      wantListeningRef.current = false
      setError('Could not start the microphone.')
    }
  }, [lang])

  const toggle = useCallback(() => {
    if (wantListeningRef.current) stop()
    else start()
  }, [start, stop])

  useEffect(
    () => () => {
      wantListeningRef.current = false
      recognitionRef.current?.abort()
    },
    [],
  )

  return { supported, listening, interim, error, start, stop, toggle }
}
