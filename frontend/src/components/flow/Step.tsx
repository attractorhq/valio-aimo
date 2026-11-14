import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Ban, CheckCircle2, Circle, Hourglass, LoaderCircle } from 'lucide-react'

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
  children,
}: {
  number: number
  title: string
  summary?: ReactNode
  status: StepStatus
  statusWord?: string
  current?: boolean
  liveStatus?: string
  children?: ReactNode
}) {
  const id = `step-${number}`
  const content = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number>()
  useLayoutEffect(() => {
    const element = content.current
    if (!element) return
    const observer = new ResizeObserver(() => setHeight(element.getBoundingClientRect().height))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
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
        <header className="step-header">
          <div>
            <h2 id={id} className="step-title">
              <span className="visually-hidden">Step {number}: </span>
              {title}
            </h2>
            <p className="step-summary" aria-live="polite">
              {status === 'running' ? (liveStatus ?? 'Working…') : summary}
            </p>
          </div>
          <StatusPill status={status} word={statusWord} />
        </header>
        <div className="step-reveal" style={{ height }}>
          <div ref={content}>
            {children && (
              <div key={status} className="step-content">
                {children}
              </div>
            )}
          </div>
        </div>
      </section>
    </li>
  )
}
