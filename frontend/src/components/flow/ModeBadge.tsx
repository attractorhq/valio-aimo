import { Database, FlaskConical, Radio } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { formatTime } from '../../lib/format'

const RECORDED_CAPTURE_TIME = '2026-11-13T21:50:00+02:00'

/** Where the data came from: live API, recorded responses, or synthetic UI mocks. */
export function ModeBadge({
  source,
  proposal,
  healthMode,
}: {
  source: DataSource
  proposal?: Proposal
  healthMode?: 'LIVE' | 'RECORDED'
}) {
  const effectiveMode = proposal?.mode ?? (source === 'GATEWAY' ? healthMode : undefined)
  const recordedTime = proposal?.recordedAt ?? RECORDED_CAPTURE_TIME

  return (
    <div>
      <div className="mode-badges" aria-label="Data mode">
        {source === 'MOCK' && (
          <span className="mode-badge mode-mock">
            <FlaskConical size={14} aria-hidden />
            Synthetic mock data
          </span>
        )}
        {effectiveMode === 'LIVE' && (
          <span className="mode-badge mode-live">
            <Radio size={14} aria-hidden />
            Live · Valio Aimo API
          </span>
        )}
        {effectiveMode === 'RECORDED' && (
          <span className="mode-badge mode-recorded">
            <Database size={14} aria-hidden />
            Recorded{recordedTime ? ` · captured ${formatTime(recordedTime)}` : ''}
          </span>
        )}
      </div>
    </div>
  )
}
