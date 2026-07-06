const MAX_CELLS = 20

interface QuotaCellsProps {
  used: number
  limit: number | null
  className?: string
}

export function QuotaCells({ used, limit, className = '' }: QuotaCellsProps) {
  if (limit === null) {
    return <p className={`text-sm text-ink/60 ${className}`}>Unlimited API creation attempts today</p>
  }

  const atLimit = used >= limit

  if (limit > MAX_CELLS) {
    return (
      <div className={className}>
        <p className="mb-1 text-sm text-ink/70">
          {used} / {limit} API creation attempts used today
          {atLimit && <span className="font-medium text-red-deep"> — limit reached</span>}
        </p>
        <MeterBar pct={(used / limit) * 100} />
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <div className="flex flex-wrap gap-1.5">
        {Array.from({ length: limit }, (_, i) => {
          const isUsed = i < used
          const cellClass = isUsed
            ? atLimit
              ? 'border-red-deep/30 bg-red'
              : 'border-green-deep/30 bg-green'
            : 'border-sand bg-cream'
          return <span key={i} className={`size-5 rounded-dot border ${cellClass}`} />
        })}
      </div>
      <span className="font-mono text-sm tabular-nums text-ink/70">
        {used} / {limit} today
        {atLimit && <span className="font-bold text-red-deep"> — limit reached</span>}
      </span>
    </div>
  )
}

export function MeterBar({ pct, className = '' }: { pct: number; className?: string }) {
  const clamped = Math.min(100, Math.max(0, pct))
  const color = clamped >= 100 ? 'bg-red' : clamped >= 80 ? 'bg-gold' : 'bg-green'
  return (
    <div className={`h-2 w-full max-w-xs rounded-pill border border-sand bg-cream ${className}`}>
      <div className={`h-full rounded-pill ${color}`} style={{ width: `${clamped}%` }} />
    </div>
  )
}
