import type { ReactNode } from 'react'

interface EmptyStateProps {
  statement: ReactNode
  description?: string
  action?: ReactNode
  className?: string
}

export default function EmptyState({ statement, description, action, className = '' }: EmptyStateProps) {
  return (
    <div className={`rounded-card border-2 border-dashed border-sand bg-cream/50 p-10 text-center ${className}`}>
      <p className="font-display text-display-sm">{statement}</p>
      {description && <p className="mt-3 text-sm text-ink/70">{description}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  )
}
