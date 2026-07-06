import type { ReactNode } from 'react'

interface StatChipProps {
  value: ReactNode
  label: string
  className?: string
}

export default function StatChip({ value, label, className = '' }: StatChipProps) {
  return (
    <div className={`inline-flex flex-col gap-0.5 rounded-card bg-ink px-5 py-3 text-paper ${className}`}>
      <span className="text-2xl font-extrabold tabular-nums leading-none">{value}</span>
      <span className="text-xs font-bold text-cream/70">{label}</span>
    </div>
  )
}
