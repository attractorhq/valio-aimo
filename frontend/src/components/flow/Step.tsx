import { Children, type ReactNode } from 'react'
import { Ban, CheckCircle2, ChevronDown, Circle, Hourglass, LoaderCircle } from 'lucide-react'

export type StepStatus = 'idle' | 'running' | 'done' | 'waiting' | 'blocked'

// Every status has an icon AND a word; colour is never the only signal.
const STATUS = {
  idle: { icon: Circle, word: 'Not started' },
  running: { icon: LoaderCircle, word: 'Running' },
  done: { icon: CheckCircle2, word: 'Done' },
  waiting: { icon: Hourglass, word: 'Waiting' },
  blocked: { icon: Ban, word: 'Blocked' },
} as const

export function StatusPill({ status, word }: { status: StepStatus; word?: string }) {
  const { icon: Icon, word: fallback } = STATUS[status]
  return (
    <span className={`status-pill status-${status}`}>
      <Icon size={14} aria-hidden className={status === 'running' ? 'spin' : undefined} />
      {word ?? fallback}
    </span>
  )
}

export function Step({
  number,
  title,
  summary,
  status,
  statusWord,
  current,
  liveStatus,
  expanded,
  onToggle,
  children,
}: {
  number: number
  title: string
  summary?: ReactNode
  status: StepStatus
  statusWord?: string
  current?: boolean
  liveStatus?: string
  expanded: boolean
  onToggle: () => void
  children?: ReactNode
}) {
  const id = `step-${number}`
  const detailsId = `${id}-details`
  const hasDetails = Children.toArray(children).length > 0
  const resolvedStatusWord =
    statusWord ?? (status === 'blocked' && number === 1 ? 'Needs detail' : undefined)
  return (
    <li
      className={`step step-${status}`}
      aria-current={current ? 'step' : undefined}
      aria-labelledby={id}
    >
      <div className="step-rail" aria-hidden>
        <span className="step-number">{number}</span>
      </div>
      <section className="step-body">
        {/* T121: the whole header opens the evidence, not only the small chevron (the button stays for keyboards
            and screen readers; a click on it or on a link inside is left to that control). */}
        <header
          className={`step-header ${hasDetails ? 'step-header-toggle' : ''}`}
          onClick={(event) => {
            if (!hasDetails) return
            if ((event.target as HTMLElement).closest('button, a, input, select, textarea')) return
            onToggle()
          }}
        >
          <div>
            <h2 id={id} className="step-title">
              <span className="visually-hidden">Step {number}: </span>
              {title}
            </h2>
            <p className="step-summary" aria-live="polite">
              {status === 'running' ? (liveStatus ?? 'Working…') : summary}
            </p>
          </div>
          <div className="step-controls">
            <StatusPill status={status} word={resolvedStatusWord} />
            {hasDetails && (
              <button
                type="button"
                className="disclosure-btn step-toggle"
                aria-label={`${title} details`}
                aria-expanded={expanded}
                aria-controls={detailsId}
                onClick={onToggle}
              >
                <ChevronDown size={16} aria-hidden />
              </button>
            )}
          </div>
        </header>
        {hasDetails && (
          <div className="step-content" id={detailsId} hidden={!expanded}>
            {children}
          </div>
        )}
      </section>
    </li>
  )
}
