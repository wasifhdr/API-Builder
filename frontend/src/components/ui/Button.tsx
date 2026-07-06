import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'default' | 'primary' | 'ink' | 'danger' | 'ghost' | 'danger-ghost'
export type ButtonSize = 'md' | 'sm'

const SIZE_PADDING: Record<ButtonSize, string> = {
  md: 'px-4 py-2 text-[15px]',
  sm: 'px-3 py-1.5 text-sm',
}

const SIZE_SHADOW: Record<ButtonSize, string> = {
  md: 'shadow-offset',
  sm: 'shadow-offset-sm',
}

const GHOST_VARIANTS = new Set<ButtonVariant>(['ghost', 'danger-ghost'])

const VARIANT_COLORS: Record<ButtonVariant, string> = {
  default: 'border-ink bg-paper text-ink hover:bg-cream',
  primary: 'border-ink bg-orange text-white hover:bg-orange-deep',
  ink: 'border-ink bg-ink text-paper hover:bg-ink/85',
  danger: 'border-ink bg-red text-white hover:bg-red-deep',
  ghost: 'border-transparent text-ink/70 hover:bg-cream hover:text-ink',
  'danger-ghost': 'border-transparent text-red-deep hover:bg-red/10',
}

export function buttonClasses(
  variant: ButtonVariant = 'default',
  size: ButtonSize = 'md',
  className = '',
) {
  const ghost = GHOST_VARIANTS.has(variant)
  return [
    'inline-flex items-center justify-center gap-2 rounded-control border-2 font-bold',
    'transition-[transform,box-shadow,background-color] duration-100',
    'focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2',
    'disabled:pointer-events-none disabled:text-ink/50',
    SIZE_PADDING[size],
    VARIANT_COLORS[variant],
    ghost
      ? ''
      : `${SIZE_SHADOW[size]} active:translate-x-[3px] active:translate-y-[3px] active:shadow-none disabled:border-ink/30 disabled:bg-sand/40 disabled:shadow-none`,
    className,
  ]
    .filter(Boolean)
    .join(' ')
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'default', size = 'md', className = '', ...props },
  ref,
) {
  return <button ref={ref} className={buttonClasses(variant, size, className)} {...props} />
})

export default Button
