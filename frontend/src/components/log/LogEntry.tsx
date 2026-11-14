import { AlertOctagon, Bot, Gavel, RefreshCcw, ShieldAlert, UserCheck } from 'lucide-react'
import {
  REASON_REGISTRY,
  type AuditEntry,
  type ProposalState,
  type ReasonCode,
} from '../../lib/contracts'
import { formatClock, formatDate, formatTime, reasonChip, reasonText } from '../../lib/format'
import { isRequirementFact } from '../../lib/requirements'
import { TrustBadge, TrustSourceMeta, formatFactDisplay } from '../trust/TrustBadge'
import { classifyActor, type ActorKind } from './actor'

const STATE_LABELS: Record<ProposalState, string> = {
  OFFERED: 'Offered',
  PROPOSED: 'Proposed',
  AUTO_APPROVED: 'Agent decided',
  PENDING_APPROVAL: 'Waiting for approver',
  APPROVED: 'Approved',
  RECHECKED: 'Re-checked live',
  CONFIRMED_SIMULATED: 'Confirmed (simulated)',
  BLOCKED: 'Blocked by mandate',
  REJECTED: 'Rejected',
  EXPIRED: 'Expired',
  SUPERSEDED: 'Superseded',
}

const EVENT_TITLES: Record<string, string> = {
  REQUEST_STRUCTURED: 'Request structured',
  OFFER_CREATED: 'Offer at a fixed price',
  PROPOSAL_CREATED: 'Proposal created',
  DECIDED: 'Gateway decision',
  POLICY_DECIDED: 'Gateway decision',
  CANDIDATE_EXCLUDED: 'Candidate excluded',
  APPROVAL_REQUESTED: 'Sign-off requested',
  APPROVED: 'Approved by a human',
  APPROVAL_GRANTED: 'Approved by a human',
  REJECTED: 'Rejected by a human',
  APPROVAL_REJECTED: 'Rejected by a human',
  RECHECKED: 'Re-checked before confirming',
  PROPOSAL_RECHECKED: 'Re-checked before confirming',
  CONFIRMED_SIMULATED: 'Confirmed (simulated)',
  ORDER_CONFIRMED_SIMULATED: 'Confirmed (simulated)',
  SUPERSEDED: 'Superseded',
  PROPOSAL_SUPERSEDED: 'Superseded',
  EXPIRED: 'Expired',
  PROPOSAL_EXPIRED: 'Expired',
  INSTRUCTION_IGNORED: 'Instruction ignored',
  MANDATE_CHANGED: 'Mandate changed',
}

const EFFECT_WORDS: Record<string, string> = {
  ASK: 'Needs sign-off',
  BLOCK: 'Blocks the order',
  EXCLUDE: 'Excludes the product',
  INFO: 'Information',
  NO_ORDER: 'No order',
}

const ACTOR_META: Record<ActorKind, { icon: typeof Bot; className: string }> = {
  AGENT: { icon: Bot, className: 'text-[var(--info)] bg-[var(--info-bg)] border-[var(--info)]' },
  GATEWAY: {
    icon: Gavel,
    className: 'text-[var(--green)] bg-[var(--green-soft)] border-[var(--green)]',
  },
  HUMAN: { icon: UserCheck, className: 'text-[var(--ask)] bg-[var(--ask-bg)] border-[var(--ask)]' },
}

function eventTitle(event: string): string {
  if (EVENT_TITLES[event]) return EVENT_TITLES[event]
  const words = event.toLowerCase().split('_').join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Fill registry template placeholders from the audit entry's facts and summary; fallback to "…". */
export function ruleText(code: ReasonCode, entry?: AuditEntry): string {
  if (!entry) {
    return reasonText(code, {}).replace(/\{\w+\}/g, '…')
  }
  const params: Record<string, string> = {}
  const facts = entry.facts ?? []

  const deliveryFact = facts.find((f) => f.fact === 'delivery.date')
  if (deliveryFact) {
    if (typeof deliveryFact.value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(deliveryFact.value)) {
      params.availableDate = formatDate(deliveryFact.value)
    } else if (deliveryFact.display && deliveryFact.display !== 'Unknown') {
      params.availableDate = deliveryFact.display.split('·')[0].trim()
    }
  }

  const reqDateFact = facts.find((f) => f.fact === 'delivery.requestedDate')
  if (reqDateFact) {
    if (typeof reqDateFact.value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(reqDateFact.value)) {
      params.requestedDate = formatDate(reqDateFact.value)
    } else if (reqDateFact.display && reqDateFact.display !== 'Unknown') {
      params.requestedDate = reqDateFact.display.replace(/^Requested delivery:\s*/i, '').trim()
    }
  } else {
    const idStr = entry.offerId ?? entry.proposalId ?? ''
    const m = idStr.match(/^(?:OF|PR)-(\d{4}-\d{2}-\d{2})-/)
    if (m) {
      params.requestedDate = formatDate(m[1])
    } else if (entry.timestamp && /^\d{4}-\d{2}-\d{2}/.test(entry.timestamp)) {
      params.requestedDate = formatDate(entry.timestamp.slice(0, 10))
    }
  }

  const cutoffFact = facts.find((f) => f.fact === 'delivery.cutoff')
  if (cutoffFact) {
    if (typeof cutoffFact.value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(cutoffFact.value)) {
      params.closingTime = formatTime(cutoffFact.value)
    } else if (cutoffFact.display && cutoffFact.display !== 'Unknown') {
      params.closingTime = cutoffFact.display
    }
  }

  const partialFact = facts.find((f) => f.fact === 'coverage.partial')
  if (partialFact && typeof partialFact.value === 'string') {
    const pm = partialFact.value.match(/^([^/]+)\/(\S+)\s+(.+)$/)
    if (pm) {
      params.available = `${pm[1]} ${pm[3]}`
      params.needed = `${pm[2]} ${pm[3]}`
      params.otherProduct = 'partial delivery'
    }
  }

  const totalMatch = entry.summary.match(/€[\d,]+(?:\.\d{2})?/)
  if (totalMatch) {
    params.total = totalMatch[0]
  }
  const limitMatch = entry.summary.match(/above (?:your |the )?(€[\d,]+(?:\.\d{2})?)/i)
  if (limitMatch) {
    params.limit = limitMatch[1]
  }

  const relFact = facts.find((f) => f.fact === 'reliability')
  if (relFact && typeof relFact.value === 'number') {
    params.pct = `${Math.round(relFact.value * 1000) / 10}%`
  }

  const subFact = facts.find((f) => f.fact === 'substitute.valio')
  if (subFact && typeof subFact.value === 'string') {
    params.product = subFact.value
    const failMatch = subFact.display.match(/fails '([^']+)'/)
    if (failMatch) {
      params.requirement = failMatch[1]
    }
  }

  return reasonText(code, params).replace(/\{\w+\}/g, '…')
}

export function isSuperseded(entry: AuditEntry): boolean {
  return entry.toState === 'SUPERSEDED' || entry.event === 'SUPERSEDED'
}

/** The server's re-check before a simulated confirmation (P1.3): it passed, or it superseded the version. */
export function isRecheck(entry: AuditEntry): boolean {
  return (
    entry.event === 'RECHECKED' ||
    entry.event === 'PROPOSAL_RECHECKED' ||
    entry.toState === 'RECHECKED' ||
    (isSuperseded(entry) && (entry.actor.includes('recheck') || /re-?check/i.test(entry.summary)))
  )
}

/** "Re-checked: no change" or "Re-check found a change: …" for one re-check entry (T47). */
export function recheckResult(entry: AuditEntry): { changed: boolean; text: string } {
  if (isSuperseded(entry)) {
    return { changed: true, text: `Re-check found a change: ${entry.summary}` }
  }
  return {
    changed: false,
    text: 'Re-checked: no change in price, stock, cut-off or mandate.',
  }
}

export function isInstructionIgnored(entry: AuditEntry): boolean {
  return (
    entry.event === 'INSTRUCTION_IGNORED' || (entry.reasons ?? []).includes('INSTRUCTION_IGNORED')
  )
}

function Callout({ entry }: { entry: AuditEntry }) {
  if (isRecheck(entry) && !isSuperseded(entry)) {
    return (
      <p
        className="flex items-start gap-2 rounded-[12px] border border-[var(--auto-accent)] bg-[var(--auto-bg)] px-3 py-2 text-sm font-semibold text-[var(--auto)]"
        data-testid="recheck-result"
      >
        <RefreshCcw size={16} aria-hidden className="mt-0.5 shrink-0" />
        <span>{recheckResult(entry).text}</span>
      </p>
    )
  }
  if (isSuperseded(entry)) {
    return (
      <p
        className="flex items-start gap-2 rounded-[12px] border border-[var(--block)] bg-[var(--block-bg)] px-3 py-2 text-sm font-semibold text-[var(--block)]"
        data-testid={isRecheck(entry) ? 'recheck-result' : undefined}
      >
        <RefreshCcw size={16} aria-hidden className="mt-0.5 shrink-0" />
        <span>
          {isRecheck(entry) ? 'Re-check found a change (see above). ' : ''}Superseded: this version
          and any approval of it are void. A new version is decided from scratch.
        </span>
      </p>
    )
  }
  if (isInstructionIgnored(entry)) {
    return (
      <p className="flex items-start gap-2 rounded-[12px] border border-[var(--ask)] bg-[var(--ask-bg)] px-3 py-2 text-sm font-semibold text-[var(--ask)]">
        <ShieldAlert size={16} aria-hidden className="mt-0.5 shrink-0" />
        <span>
          Instruction ignored: request text is data. Sign-off only comes through the approval
          channel.
        </span>
      </p>
    )
  }
  return null
}

/** One decision-log entry: one-line table row summary, with full rules and facts on expand. */
export function LogEntry({ entry }: { entry: AuditEntry }) {
  const actor = classifyActor(entry.actor)
  const { icon: ActorIcon, className } = ACTOR_META[actor.kind]
  const reasons = entry.reasons ?? []
  const facts = entry.facts ?? []
  // Allergen and claim facts first: for POLICY_DECIDED they include the requirement evidence the
  // policy decided on (T41), the same facts the trust cards show.
  const factGroups = [
    { title: 'Allergen and claim facts', items: facts.filter(isRequirementFact) },
    { title: 'Other facts', items: facts.filter((f) => !isRequirementFact(f)) },
  ].filter((group) => group.items.length > 0)
  const highlighted = isSuperseded(entry) || isInstructionIgnored(entry)
  const metaParts: string[] = []
  if (entry.rule) metaParts.push(entry.rule)
  if (facts.length > 0) metaParts.push(`${facts.length} fact${facts.length === 1 ? '' : 's'}`)

  return (
    <li
      className={`log-entry ${highlighted ? 'log-entry-highlight' : ''}`}
      aria-label={`${formatClock(entry.timestamp)} ${eventTitle(entry.event)}`}
    >
      <details className="log-details">
        <summary className="log-row">
          <time
            dateTime={entry.timestamp}
            title={formatTime(entry.timestamp)}
            className="log-col-time"
          >
            {formatClock(entry.timestamp)}
          </time>
          <span className="log-col-actor">
            <span
              className={`inline-flex items-center gap-1 rounded-[12px] border px-2 py-0.5 text-[11px] font-bold tracking-wide uppercase ${className}`}
            >
              <ActorIcon size={11} aria-hidden />
              <span>{actor.word}</span>
            </span>
          </span>
          <span className="log-col-event">
            <span className="font-semibold text-[var(--ink)]">{eventTitle(entry.event)}</span>
          </span>
          <span className="log-col-summary">
            <span className="log-summary-text">{entry.summary}</span>
            {entry.comment && (
              <span className="text-xs italic text-[var(--muted)]"> “{entry.comment}”</span>
            )}
            {reasons.length > 0 && (
              <span className="inline-flex flex-wrap gap-1 align-middle">
                {reasons.map((code) => (
                  <span key={code} className="chip log-inline-chip">
                    {reasonChip(code)}
                  </span>
                ))}
              </span>
            )}
          </span>
          <span className="log-col-meta">
            {metaParts.length > 0 && (
              <span className="log-meta-pill">{metaParts.join(' · ')}</span>
            )}
            <span className="log-chevron" aria-hidden="true">
              ▾
            </span>
          </span>
        </summary>

        <div className="log-expanded">
          {(highlighted || isRecheck(entry)) && (
            <div className="mb-2.5">
              <Callout entry={entry} />
            </div>
          )}

          <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
            <dt className="font-semibold text-[var(--muted)]">Actor</dt>
            <dd>{actor.who}</dd>
            {(entry.fromState || entry.toState) && (
              <>
                <dt className="font-semibold text-[var(--muted)]">State</dt>
                <dd>
                  {entry.fromState ? STATE_LABELS[entry.fromState] : 'Start'} →{' '}
                  <strong>{entry.toState ? STATE_LABELS[entry.toState] : 'unchanged'}</strong>
                </dd>
              </>
            )}
            {entry.rule && (
              <>
                <dt className="font-semibold text-[var(--muted)]">Rule</dt>
                <dd>{entry.rule}</dd>
              </>
            )}
            {entry.offerVersion != null && (
              <>
                <dt className="font-semibold text-[var(--muted)]">Bound to</dt>
                <dd className="break-all">
                  offer v{entry.offerVersion}
                  {entry.offerHash && (
                    <span className="font-mono text-xs text-[var(--muted)]">
                      {' '}
                      · hash {entry.offerHash.slice(0, 12)}…
                    </span>
                  )}
                  {' · '}mandate v{entry.mandateVersion}
                </dd>
              </>
            )}
          </dl>

          {reasons.length > 0 && (
            <div className="mt-2.5">
              <h4 className="text-xs font-semibold text-[var(--muted)]">Reasons</h4>
              <ul className="mt-1 grid gap-1.5">
                {reasons.map((code) => (
                  <li key={code} className="text-xs">
                    <span className="inline-flex items-center gap-1 rounded border border-[var(--line)] bg-[var(--bg)] px-1.5 py-0.5 font-semibold">
                      <AlertOctagon size={11} aria-hidden />
                      {reasonChip(code)}
                    </span>{' '}
                    <code className="text-[11px]">{code}</code>{' '}
                    <span className="text-[var(--muted)]">
                      · {EFFECT_WORDS[REASON_REGISTRY[code].effect] ?? REASON_REGISTRY[code].effect}
                    </span>
                    <p className="mt-0.5 text-[var(--muted)]">{ruleText(code, entry)}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {facts.length > 0 && (
            <div className="mt-2.5">
              <h4 className="text-xs font-semibold text-[var(--muted)]">Facts used</h4>
              {factGroups.map((group) => (
                <section key={group.title} aria-label={group.title} className="mt-1">
                  {factGroups.length > 1 && (
                    <h5 className="text-[11px] font-semibold text-[var(--muted)]">{group.title}</h5>
                  )}
                  <ul className="mt-1 grid gap-1.5">
                    {group.items.map((fact, index) => (
                      <li key={`${fact.fact}-${index}`} className="grid gap-0.5 text-xs">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <TrustBadge level={fact.level} conflict={fact.conflict} />
                          <span className="font-semibold">{formatFactDisplay(fact)}</span>
                          <code className="text-[11px] text-[var(--muted)]">{fact.fact}</code>
                        </span>
                        <TrustSourceMeta fact={fact} />
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </div>
      </details>
    </li>
  )
}
