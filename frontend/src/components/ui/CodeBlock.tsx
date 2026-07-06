import { useState } from 'react'
import type { ReactNode } from 'react'

interface CodeBlockProps {
  lang: string
  code: string
  className?: string
}

export function CodeBlock({ lang, code, className = '' }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard access denied — silently ignore, copy button just won't confirm
    }
  }

  return (
    <div className={`overflow-hidden rounded-card border-2 border-ink bg-ink ${className}`}>
      <div className="flex items-center justify-between border-b border-cream/15 px-4 py-2">
        <span className="text-label uppercase text-cream/60">{lang}</span>
        <button
          type="button"
          onClick={copy}
          className="rounded-dot border border-cream/30 px-2 py-1 text-xs font-bold text-cream hover:bg-cream/10 focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-cream">{code}</pre>
    </div>
  )
}

export function InlineCode({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-dot border border-sand bg-cream px-1.5 py-0.5 font-mono text-[0.9em]">
      {children}
    </code>
  )
}
