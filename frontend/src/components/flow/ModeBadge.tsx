import { Database, FlaskConical } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { formatTime } from '../../lib/format'
import { RECORDED_CAPTURE_TIME } from '../../pages/Eval'

/**
 * Labels data that is NOT the live Valio Aimo API: synthetic UI mocks or recorded responses (P5.3: recorded
 * mode is always clearly labelled). Live data needs no badge. The live pre-flight is a presenter command
 * (`scripts/preflight`), not a buyer-view control.
 */
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
  const mock = source === 'MOCK'
  const recorded = effectiveMode === 'RECORDED'
  if (!mock && !recorded) return null

  return (
    <div className="mode-badges" aria-label="Data mode">
      {mock && (
        <span className="mode-badge mode-mock">
          <FlaskConical size={14} aria-hidden />
          Synthetic mock data
        </span>
      )}
      {recorded && (
        <span className="mode-badge mode-recorded">
          <Database size={14} aria-hidden />
          Recorded{recordedTime ? ` · captured ${formatTime(recordedTime)}` : ''}
        </span>
      )}
    </div>
  )
}
