import { Database, FlaskConical, Radio } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { formatTime } from '../../lib/format'

/** Where the data came from: live API, recorded responses, or synthetic UI mocks. Always visible. */
export function ModeBadge({ source, proposal }: { source: DataSource; proposal?: Proposal }) {
  return (
    <div className="mode-badges" aria-label="Data mode">
      {source === 'MOCK' && (
        <span className="mode-badge mode-mock">
          <FlaskConical size={14} aria-hidden />
          Synthetic mock data
        </span>
      )}
      {proposal?.mode === 'LIVE' && (
        <span className="mode-badge mode-live">
          <Radio size={14} aria-hidden />
          Live · Valio Aimo API
        </span>
      )}
      {proposal?.mode === 'RECORDED' && (
        <span className="mode-badge mode-recorded">
          <Database size={14} aria-hidden />
          Recorded{proposal.recordedAt ? ` · captured ${formatTime(proposal.recordedAt)}` : ''}
        </span>
      )}
    </div>
  )
}
