import type { HTMLAttributes } from 'react'

export type CardVariant = 'feature' | 'standard' | 'quiet' | 'callout'
export type Accent = 'orange' | 'green' | 'blue' | 'gold' | 'purple' | 'red'

const ACCENT_L: Record<Accent, string> = {
  orange: 'border-l-orange',
  green: 'border-l-green',
  blue: 'border-l-blue',
  gold: 'border-l-gold',
  purple: 'border-l-purple',
  red: 'border-l-red',
}

const ACCENT_T: Record<Accent, string> = {
  orange: 'border-t-orange',
  green: 'border-t-green',
  blue: 'border-t-blue',
  gold: 'border-t-gold',
  purple: 'border-t-purple',
  red: 'border-t-red',
}

export interface CardClassOpts {
  variant?: CardVariant
  /** callout: left-border color (default gold). feature/standard: top-border accent (plan cards). */
  accent?: Accent
  clickable?: boolean
  className?: string
}

export function cardClasses({ variant = 'standard', accent, clickable, className = '' }: CardClassOpts = {}) {
  const parts: string[] = ['rounded-card bg-paper']
  switch (variant) {
    case 'feature':
      parts.push('border-2 border-ink p-6 shadow-offset-lg')
      break
    case 'standard':
      parts.push('border-2 border-ink p-5 shadow-offset')
      break
    case 'quiet':
      parts.push('border border-sand p-5')
      break
    case 'callout':
      parts.push('rounded-control border border-sand border-l-4 bg-cream p-4', ACCENT_L[accent ?? 'gold'])
      break
  }
  if (accent && (variant === 'feature' || variant === 'standard')) {
    parts.push('border-t-4', ACCENT_T[accent])
  }
  if (clickable) {
    parts.push(
      'block transition-[transform,box-shadow] duration-100 hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-offset-lg',
    )
  }
  parts.push(className)
  return parts.filter(Boolean).join(' ')
}

interface CardProps extends HTMLAttributes<HTMLDivElement>, CardClassOpts {}

export default function Card({ variant, accent, clickable, className, children, ...rest }: CardProps) {
  return (
    <div className={cardClasses({ variant, accent, clickable, className })} {...rest}>
      {children}
    </div>
  )
}
