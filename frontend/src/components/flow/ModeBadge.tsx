import { useState } from 'react'
import { Activity, Database, FlaskConical, Radio } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { formatTime } from '../../lib/format'
import { Eval, RECORDED_CAPTURE_TIME, type PresentationMode } from '../../pages/Eval'

/** Where the data came from: live API, recorded responses, or synthetic UI mocks, plus the Pre-flight panel. */
export function ModeBadge({
  source,
  proposal,
  healthMode,
}: {
  source: DataSource
  proposal?: Proposal
  healthMode?: 'LIVE' | 'RECORDED'
}) {
  const [showPreflight, setShowPreflight] = useState(false)
  const [overrideMode, setOverrideMode] = useState<PresentationMode | null>(null)

  const effectiveMode =
    overrideMode ?? proposal?.mode ?? (source === 'GATEWAY' ? healthMode : undefined)
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
        {/* T57: no eval score in the buyer view. It is a developer metric, kept inside Pre-flight. */}
        <button
          type="button"
          aria-expanded={showPreflight}
          onClick={() => setShowPreflight((prev) => !prev)}
          className="mode-badge cursor-pointer border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:border-[var(--green)]"
        >
          <Activity size={13} aria-hidden />
          <span>Pre-flight</span>
        </button>
      </div>
      {showPreflight && (
        <Eval mode={effectiveMode ?? 'RECORDED'} onModeChange={(next) => setOverrideMode(next)} />
      )}
    </div>
  )
}
