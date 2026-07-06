import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react'

export function TableWrapper({ className = '', ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`overflow-x-auto rounded-card border border-sand bg-paper ${className}`} {...props} />
}

export function Table({ className = '', ...props }: HTMLAttributes<HTMLTableElement>) {
  return <table className={`w-full text-sm ${className}`} {...props} />
}

export function Th({ className = '', ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={`border-b-2 border-ink px-3 py-2 text-left text-label uppercase text-ink/60 ${className}`}
      {...props}
    />
  )
}

interface TdProps extends TdHTMLAttributes<HTMLTableCellElement> {
  mono?: boolean
}

export function Td({ className = '', mono, ...props }: TdProps) {
  return <td className={`px-3 py-2.5 ${mono ? 'font-mono text-[13px] tabular-nums' : ''} ${className}`} {...props} />
}

export function Tr({ className = '', ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={`border-b border-sand last:border-0 hover:bg-cream/60 ${className}`} {...props} />
}

export function EmptyRow({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className="py-8 text-center text-sm text-ink/60">
        {children}
      </td>
    </tr>
  )
}
