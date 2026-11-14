import { AlertTriangle, FileCheck2, Gauge, HelpCircle, ShieldCheck } from 'lucide-react'
import type { Conflict, TrustLabel, TrustLevel } from '../../lib/contracts'
import { formatTime } from '../../lib/format'

const LEVEL_META: Record<
  TrustLevel,
  {
    icon: typeof ShieldCheck
    word: string
    className: string
  }
> = {
  VERIFIED: {
    icon: ShieldCheck,
    word: 'Verified',
    className: 'text-[var(--auto)] bg-[var(--auto-bg)] border-[var(--auto)]',
  },
  DECLARED: {
    icon: FileCheck2,
    word: 'Declared',
    className: 'text-[var(--info)] bg-[var(--info-bg)] border-[var(--info)]',
  },
  ESTIMATE: {
    icon: Gauge,
    word: 'Estimate',
    className: 'text-[var(--ask)] bg-[var(--ask-bg)] border-[var(--ask)]',
  },
  UNKNOWN: {
    icon: HelpCircle,
    word: 'Unknown',
    className: 'text-[var(--muted)] bg-[var(--bg)] border-[var(--muted)] border-dashed',
  },
}

/**
 * Agent Trust Labels v0.1 badge: always renders icon + word + border style, never colour alone (§5.4).
 */
export function TrustBadge({
  level,
  conflict,
}: {
  level: TrustLevel
  conflict?: Conflict | null
}) {
  const { icon: Icon, word, className } = LEVEL_META[level]
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span
        className={`inline-flex items-center gap-1 rounded-[12px] border px-2 py-0.5 text-xs font-bold tracking-wide uppercase ${className}`}
      >
        <Icon size={12} aria-hidden />
        <span>{word}</span>
      </span>
      {conflict && (
        <span className="inline-flex items-center gap-1 rounded-[12px] border border-[var(--block)] bg-[var(--block-bg)] px-2 py-0.5 text-xs font-bold tracking-wide text-[var(--block)] uppercase">
          <AlertTriangle size={12} aria-hidden />
          <span>Conflict · {conflict.code}</span>
        </span>
      )}
    </span>
  )
}

/**
 * §5.4: UNKNOWN is always shown as "Unknown", never blank.
 */
export function formatFactDisplay(fact?: TrustLabel | null): string {
  if (!fact) return 'Unknown'
  const text = (fact.display ?? '').trim()
  if (fact.level === 'UNKNOWN') {
    if (!text || text.toLowerCase() === 'unknown') return 'Unknown'
    return text.toLowerCase().startsWith('unknown') ? text : `Unknown (${text})`
  }
  return text || 'Unknown'
}

/**
 * Renders the source system, field/method, and live retrieval timestamp (Europe/Helsinki) for a TrustLabel.
 */
export function TrustSourceMeta({ fact }: { fact: TrustLabel }) {
  const { source, method } = fact
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[var(--muted)]">
      <span>
        {source.system}
        {source.field ? ` · ${source.field}` : ''}
      </span>
      {method && <span>({method})</span>}
      {source.retrievedAt && <time dateTime={source.retrievedAt}>{formatTime(source.retrievedAt)}</time>}
    </span>
  )
}
