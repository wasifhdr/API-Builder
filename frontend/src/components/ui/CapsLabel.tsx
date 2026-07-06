import type { HTMLAttributes } from 'react'

export type CapsLabelTone = 'orange' | 'green' | 'blue' | 'gold' | 'purple' | 'red' | 'muted'

const TONE_CLASSES: Record<CapsLabelTone, string> = {
  orange: 'text-orange-deep',
  green: 'text-green-deep',
  blue: 'text-blue-deep',
  gold: 'text-gold-deep',
  purple: 'text-purple-deep',
  red: 'text-red-deep',
  muted: 'text-ink/60',
}

interface CapsLabelProps extends HTMLAttributes<HTMLParagraphElement> {
  tone?: CapsLabelTone
}

export default function CapsLabel({ tone = 'orange', className = '', ...props }: CapsLabelProps) {
  return <p className={`text-label uppercase ${TONE_CLASSES[tone]} ${className}`} {...props} />
}
