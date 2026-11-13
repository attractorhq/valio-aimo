import type { AuditEntry } from '../../lib/contracts'
import { LogEntry } from './LogEntry'

/** Chronological timeline of one proposal: request → offer → decision → final state. */
export function DecisionLog({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) {
    return <p className="notice">No log entries yet for this proposal.</p>
  }
  const ordered = [...entries].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )
  return (
    <ol className="grid gap-3" aria-label="Decision log">
      {ordered.map((entry) => (
        <LogEntry key={entry.entryId} entry={entry} />
      ))}
    </ol>
  )
}
