import type { ReactNode } from 'react'

export type BadgeVariant = 'neutral' | 'success' | 'pending' | 'failed' | 'info' | 'purple'

const BADGE_VARIANTS: Record<BadgeVariant, string> = {
  neutral: 'border-sand bg-cream text-ink/70',
  success: 'border-green/40 bg-green/10 text-green-deep',
  pending: 'border-gold/50 bg-gold/15 text-gold-deep',
  failed: 'border-red/40 bg-red/10 text-red-deep',
  info: 'border-blue/40 bg-blue/10 text-blue-deep',
  purple: 'border-purple/40 bg-purple/10 text-purple-deep',
}

interface BadgeProps {
  variant?: BadgeVariant
  className?: string
  children: ReactNode
  /** Prepend a pulsing dot — use for live/recording indicators. */
  pulse?: boolean
}

export default function Badge({ variant = 'neutral', className = '', children, pulse }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-label uppercase ${BADGE_VARIANTS[variant]} ${className}`}
    >
      {pulse && <span className="size-2 rounded-pill bg-red animate-pulse" />}
      {children}
    </span>
  )
}
