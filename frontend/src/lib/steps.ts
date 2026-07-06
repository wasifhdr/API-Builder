import type { Step, StepValue } from './types'

export function stepValueLabel(value?: StepValue): string {
  if (!value) return ''
  if ('param' in value) return `{${value.param}}`
  return `"${value.literal}"`
}

export function describeStep(step: Step): string {
  const selector = step.selectors?.[0] ?? ''
  switch (step.type) {
    case 'goto':
      return `Go to ${step.url}`
    case 'click':
      return `Click ${selector}`
    case 'fill':
      return `Fill ${selector} = ${stepValueLabel(step.value)}`
    case 'press':
      return `Press ${step.key} on ${selector}`
    case 'select_option':
      return `Select ${stepValueLabel(step.value)} in ${selector}`
    case 'extract':
      return 'Extract data'
    case 'scroll_page':
      return 'Scroll for more results'
    default:
      return step.type
  }
}
