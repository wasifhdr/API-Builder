import type { ReactNode } from 'react'

interface PageHeaderProps {
  eyebrow?: ReactNode
  title: ReactNode
  subline?: ReactNode
  actions?: ReactNode
  className?: string
}

export default function PageHeader({ eyebrow, title, subline, actions, className = '' }: PageHeaderProps) {
  return (
    <div className={`mb-8 flex flex-wrap items-start justify-between gap-4 ${className}`}>
      <div>
        {eyebrow}
        <h1 className="text-h1">{title}</h1>
        {subline && <p className="mt-1 text-sm text-ink/70">{subline}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
    </div>
  )
}
