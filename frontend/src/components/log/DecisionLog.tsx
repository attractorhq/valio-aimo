import type { AuditEntry } from '../../lib/contracts'
import { LogEntry } from './LogEntry'

/** Chronological timeline of one proposal: request → offer → decision → final state. */
export function DecisionLog({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) {
    return <p className="notice">No log entries yet for this proposal.</p>
  }
  // The audit log is append-only, so the gateway's order is the true sequence. Do not re-sort by
  // timestamp: in recorded mode, entries stamped with the capture time and with the API's own
  // `timeNow` can be a second apart in the wrong direction.
  return (
    <div className="log-table-wrap">
      <div className="log-table-head" aria-hidden="true">
        <span>Time</span>
        <span>Actor</span>
        <span>Event</span>
        <span>Summary</span>
        <span className="text-right">Details</span>
      </div>
      <ol className="log-list" aria-label="Decision log">
        {entries.map((entry) => (
          <LogEntry key={entry.entryId} entry={entry} />
        ))}
      </ol>
    </div>
  )
}
