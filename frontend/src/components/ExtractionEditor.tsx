import { cardClasses } from './ui'
import type { ExtractionConfig, ExtractionField } from '../lib/types'

interface Props {
  extraction: ExtractionConfig
  onChange: (next: ExtractionConfig) => void
  disabled?: boolean
}

const CELL_INPUT =
  'w-full rounded-dot border border-sand bg-paper px-1.5 py-1 text-xs focus-visible:outline-2 focus-visible:outline-ink disabled:bg-cream disabled:text-ink/50'

export default function ExtractionEditor({ extraction, onChange, disabled }: Props) {
  function updateField(index: number, patch: Partial<ExtractionField>) {
    const fields = extraction.fields.map((f, i) => (i === index ? { ...f, ...patch } : f))
    onChange({ ...extraction, fields })
  }

  function removeField(index: number) {
    onChange({ ...extraction, fields: extraction.fields.filter((_, i) => i !== index) })
  }

  function addBlankField() {
    onChange({
      ...extraction,
      fields: [
        ...extraction.fields,
        { name: `field${extraction.fields.length + 1}`, description: '', selector: '', take: 'text', transform: 'none' },
      ],
    })
  }

  return (
    <div className={cardClasses({ variant: 'quiet', className: 'space-y-3' })}>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left">
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Name</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Description</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Selector</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Take</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Transform</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {extraction.fields.map((field, i) => (
            <tr key={i}>
              <td className="py-1 pr-1.5">
                <input
                  type="text"
                  disabled={disabled}
                  value={field.name}
                  onChange={(e) => updateField(i, { name: e.target.value })}
                  className={CELL_INPUT}
                />
              </td>
              <td className="py-1 pr-1.5">
                <input
                  type="text"
                  disabled={disabled}
                  value={field.description ?? ''}
                  placeholder="what this field is, e.g. starting price in BDT"
                  onChange={(e) => updateField(i, { description: e.target.value })}
                  className={CELL_INPUT}
                />
              </td>
              <td className="py-1 pr-1.5">
                <span className="block max-w-[220px] truncate font-mono text-[11px] text-ink/60">
                  {field.selectors?.[0] ?? field.selector ?? '—'}
                </span>
              </td>
              <td className="py-1 pr-1.5">
                <select
                  disabled={disabled}
                  value={field.take}
                  onChange={(e) => updateField(i, { take: e.target.value })}
                  className={CELL_INPUT}
                >
                  <option value="text">text</option>
                  <option value="html">html</option>
                  <option value="attr:href">attr:href</option>
                  <option value="attr:src">attr:src</option>
                </select>
              </td>
              <td className="py-1 pr-1.5">
                <select
                  disabled={disabled}
                  value={field.transform ?? 'none'}
                  onChange={(e) => updateField(i, { transform: e.target.value })}
                  className={CELL_INPUT}
                >
                  <option value="none">none</option>
                  <option value="number">number</option>
                  <option value="abs_url">abs_url</option>
                  <option value="trim">trim</option>
                </select>
              </td>
              <td className="py-1">
                {!disabled && (
                  <button type="button" onClick={() => removeField(i)} className="font-bold text-red-deep hover:text-red">
                    &times;
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!disabled && (
        <button type="button" onClick={addBlankField} className="text-xs font-bold text-ink/70 hover:text-ink">
          + Add field
        </button>
      )}
    </div>
  )
}
