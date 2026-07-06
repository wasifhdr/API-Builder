import { forwardRef } from 'react'
import type {
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

const CONTROL_BASE =
  'w-full rounded-control border-2 border-ink bg-paper px-3.5 py-2 text-[15px] placeholder:text-ink/45 focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2 disabled:border-ink/30 disabled:bg-cream disabled:text-ink/50'
const CONTROL_ERROR = 'border-red focus-visible:outline-red'

export function FieldLabel({ className = '', ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={`mb-1.5 block text-label uppercase text-ink/70 ${className}`} {...props} />
}

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className = '', error, ...props },
  ref,
) {
  return <input ref={ref} className={`${CONTROL_BASE} ${error ? CONTROL_ERROR : ''} ${className}`} {...props} />
})

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  error?: boolean
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className = '', error, ...props },
  ref,
) {
  return <select ref={ref} className={`${CONTROL_BASE} ${error ? CONTROL_ERROR : ''} ${className}`} {...props} />
})

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className = '', error, ...props },
  ref,
) {
  return <textarea ref={ref} className={`${CONTROL_BASE} ${error ? CONTROL_ERROR : ''} ${className}`} {...props} />
})

export function Checkbox({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="checkbox" className={`size-4 rounded-dot border-2 border-ink accent-orange ${className}`} {...props} />
}

export function Radio({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="radio" className={`size-4 border-2 border-ink accent-orange ${className}`} {...props} />
}

export function FieldError({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-xs font-medium text-red-deep">{children}</p>
}

export function FieldHelp({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-xs text-ink/60">{children}</p>
}
