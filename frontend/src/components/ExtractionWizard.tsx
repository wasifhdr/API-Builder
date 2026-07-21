import { useState } from 'react'
import type { CompiledField, ExtractionField, PickCandidate, WizardStep } from '../lib/types'
import { Button, cardClasses } from './ui'

interface ExtractionWizardProps {
  step: WizardStep
  mode: 'single' | 'list'
  fields: ExtractionField[]
  pickResult: PickCandidate | null
  lastCompiled: CompiledField | null
  disabled: boolean
  onStart: () => void
  onChooseMode: (mode: 'single' | 'list') => void
  onConfirmRoot: () => void
  onCompileValue: (name: string, description: string, take: string) => void
  onAddField: () => void
  onUndoPick: () => void
  onFinish: () => void
  onCancel: () => void
}

const TAKES = ['text', 'attr:href', 'attr:src', 'html']

export default function ExtractionWizard({
  step, mode, fields, pickResult, lastCompiled, disabled,
  onStart, onChooseMode, onConfirmRoot, onCompileValue, onAddField,
  onUndoPick, onFinish, onCancel,
}: ExtractionWizardProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [take, setTake] = useState('text')

  function handleUndo() {
    onUndoPick()
    setName('')
    setDescription('')
    setTake('text')
  }

  if (step === 'idle') {
    return (
      <Button variant="ink" size="sm" onClick={onStart} disabled={disabled}>
        Pick element
      </Button>
    )
  }

  return (
    <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} space-y-2`}>
      {step === 'choose-mode' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">What are you extracting?</p>
          <div className="flex gap-2">
            <Button variant="ink" size="sm" onClick={() => onChooseMode('single')}>Single record</Button>
            <Button variant="ink" size="sm" onClick={() => onChooseMode('list')}>List of records</Button>
          </div>
        </div>
      )}

      {step === 'pick-root' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">Click one repeating row in the browser, then confirm the root.</p>
          {pickResult && (
            <>
              <p className="truncate font-mono text-xs text-ink/80">{pickResult.selectors[0]}</p>
              <p className="text-xs text-ink/60">{pickResult.count} similar element(s)</p>
              <div className="flex gap-2">
                <Button variant="ink" size="sm" onClick={onConfirmRoot}>Use as root</Button>
                <Button size="sm" onClick={handleUndo}>Undo pick</Button>
              </div>
            </>
          )}
        </div>
      )}

      {step === 'choose-values' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">
            Click a value in the browser{mode === 'list' ? ' (inside a row)' : ''}, name it, then add it.
          </p>
          {fields.length > 0 && (
            <ul className="space-y-0.5 text-xs text-ink/70">
              {fields.map((f) => (
                <li key={f.name} className="font-mono">✓ {f.name}</li>
              ))}
            </ul>
          )}
          {pickResult && (
            <div className="space-y-1.5">
              <p className="text-xs text-ink/60">
                Picked{pickResult.preview ? `: "${pickResult.preview.slice(0, 40)}"` : ''}
              </p>
              <input
                type="text" value={name} disabled={disabled}
                placeholder="field name, e.g. price"
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              />
              <input
                type="text" value={description} disabled={disabled}
                placeholder="what it is, e.g. nightly price in BDT"
                onChange={(e) => setDescription(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              />
              <select
                value={take} disabled={disabled}
                onChange={(e) => setTake(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              >
                {TAKES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <div className="flex gap-2">
                {lastCompiled ? (
                  <Button variant="ink" size="sm" onClick={() => { onAddField(); setName(''); setDescription(''); setTake('text') }}>
                    Add this value
                  </Button>
                ) : (
                  <Button variant="ink" size="sm" disabled={disabled || !name} onClick={() => onCompileValue(name, description, take)}>
                    Compile selector
                  </Button>
                )}
                <Button size="sm" onClick={handleUndo}>Undo pick</Button>
              </div>
              {lastCompiled && (
                <p className="truncate font-mono text-[11px] text-ink/60">{lastCompiled.selectors[0]}</p>
              )}
            </div>
          )}
          <div className="flex gap-2 border-t border-sand pt-2">
            <Button variant="primary" size="sm" disabled={fields.length === 0} onClick={onFinish}>Done</Button>
            <Button variant="danger-ghost" size="sm" onClick={onCancel}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  )
}
