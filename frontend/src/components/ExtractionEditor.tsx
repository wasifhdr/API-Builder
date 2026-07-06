import type { ExtractionConfig, ExtractionField } from '../lib/types'

interface Props {
  extraction: ExtractionConfig
  onChange: (next: ExtractionConfig) => void
  disabled?: boolean
}

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
        { name: `field${extraction.fields.length + 1}`, selector: '', take: 'text', transform: 'none' },
      ],
    })
  }

  return (
    <div className="space-y-3 rounded-md border border-gray-200 p-3">
      <div className="flex items-center gap-3 text-sm">
        <label className="flex items-center gap-1">
          <input
            type="radio"
            disabled={disabled}
            checked={extraction.mode === 'single'}
            onChange={() => onChange({ ...extraction, mode: 'single' })}
          />
          Single
        </label>
        <label className="flex items-center gap-1">
          <input
            type="radio"
            disabled={disabled}
            checked={extraction.mode === 'list'}
            onChange={() => onChange({ ...extraction, mode: 'list' })}
          />
          List
        </label>
        {extraction.mode === 'list' && (
          <input
            type="text"
            disabled={disabled}
            value={extraction.root ?? ''}
            onChange={(e) => onChange({ ...extraction, root: e.target.value })}
            placeholder="root selector, e.g. .book-item"
            className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs font-mono disabled:bg-gray-50"
          />
        )}
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500">
            <th className="pb-1">Name</th>
            <th className="pb-1">Selector</th>
            <th className="pb-1">Take</th>
            <th className="pb-1">Transform</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {extraction.fields.map((field, i) => (
            <tr key={i}>
              <td className="pr-1 py-1">
                <input
                  type="text"
                  disabled={disabled}
                  value={field.name}
                  onChange={(e) => updateField(i, { name: e.target.value })}
                  className="w-full rounded border border-gray-300 px-1 py-0.5 disabled:bg-gray-50"
                />
              </td>
              <td className="pr-1 py-1">
                <input
                  type="text"
                  disabled={disabled}
                  value={field.selector}
                  onChange={(e) => updateField(i, { selector: e.target.value })}
                  className="w-full rounded border border-gray-300 px-1 py-0.5 font-mono disabled:bg-gray-50"
                />
              </td>
              <td className="pr-1 py-1">
                <select
                  disabled={disabled}
                  value={field.take}
                  onChange={(e) => updateField(i, { take: e.target.value })}
                  className="w-full rounded border border-gray-300 px-1 py-0.5 disabled:bg-gray-50"
                >
                  <option value="text">text</option>
                  <option value="html">html</option>
                  <option value="attr:href">attr:href</option>
                  <option value="attr:src">attr:src</option>
                </select>
              </td>
              <td className="pr-1 py-1">
                <select
                  disabled={disabled}
                  value={field.transform ?? 'none'}
                  onChange={(e) => updateField(i, { transform: e.target.value })}
                  className="w-full rounded border border-gray-300 px-1 py-0.5 disabled:bg-gray-50"
                >
                  <option value="none">none</option>
                  <option value="number">number</option>
                  <option value="abs_url">abs_url</option>
                  <option value="trim">trim</option>
                </select>
              </td>
              <td className="py-1">
                {!disabled && (
                  <button type="button" onClick={() => removeField(i)} className="text-red-500">
                    &times;
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!disabled && (
        <button type="button" onClick={addBlankField} className="text-xs text-gray-600 hover:text-gray-900">
          + Add field
        </button>
      )}
    </div>
  )
}
