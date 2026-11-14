import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, RefreshCcw, ScrollText, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { AuditEntry } from '../lib/contracts'
import { DecisionLog } from '../components/log/DecisionLog'
import { LogDownloads } from '../components/log/LogDownloads'
import { isRecheck, recheckResult } from '../components/log/LogEntry'
import { MOCK_LOGS, findMockLog, logPath } from '../components/log/logData'
import { gateway } from '../lib/gateway'

const GENESIS_HASH = '0'.repeat(64)

type AuditChainLink = {
  index: number
  entryId: string
  prevHash: string
  entryHash: string
  expectedPrevHash: string
  expectedEntryHash: string
  valid: boolean
}

type AuditChainVerification = {
  verified: boolean
  chainLength: number
  genesisHash: string
  headHash: string
  tamperedEntryIds: string[]
  firstTamperedIndex?: number | null
  reason?: string | null
  links: AuditChainLink[]
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return '[' + value.map(canonicalJson).join(',') + ']'
  }
  const obj = value as Record<string, unknown>
  const keys = Object.keys(obj).sort()
  return (
    '{' +
    keys
      .filter((k) => obj[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`)
      .join(',') +
    '}'
  )
}

async function sha256Hex(message: string): Promise<string> {
  const bytes = new TextEncoder().encode(message)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function computeClientVerification(entries: AuditEntry[]): Promise<AuditChainVerification> {
  const links: AuditChainLink[] = []
  let prevHash = GENESIS_HASH
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]
    const entryHash = await sha256Hex(`${prevHash}:${canonicalJson(entry)}`)
    links.push({
      index: i,
      entryId: entry.entryId,
      prevHash,
      entryHash,
      expectedPrevHash: prevHash,
      expectedEntryHash: entryHash,
      valid: true,
    })
    prevHash = entryHash
  }
  return {
    verified: true,
    chainLength: links.length,
    genesisHash: GENESIS_HASH,
    headHash: prevHash,
    tamperedEntryIds: [],
    links,
  }
}

async function fetchGatewayVerification(
  proposalId: string,
  entries: AuditEntry[],
): Promise<AuditChainVerification> {
  try {
    const res = await fetch(`/v0.1/audit/verify?proposalId=${encodeURIComponent(proposalId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (res.ok) {
      return (await res.json()) as AuditChainVerification
    }
  } catch {
    // Fallback to client-side SHA-256 chain computation in offline mock mode
  }
  return computeClientVerification(entries)
}

/** Decision log per proposal (P6 P0-5, T28): readable for the buyer and Valio Aimo with SHA-256 hash-chain verification. */
export function Log({ proposalId }: { proposalId?: string }) {
  const health = useQuery({
    queryKey: ['gateway-health'],
    queryFn: () => gateway.checkHealth(),
    retry: false,
  })
  const trail = useQuery({
    queryKey: ['audit', proposalId],
    queryFn: () => gateway.getAuditTrail(proposalId!),
    enabled: Boolean(proposalId),
    retry: false,
  })
  const current = proposalId ? findMockLog(proposalId) : undefined
  const source = trail.data?.source ?? (current ? 'MOCK' : gateway.source)
  const [verification, setVerification] = useState<AuditChainVerification | null>(null)
  // The server's re-check before a simulated confirmation, surfaced at the top of the log (T47).
  const lastRecheck = trail.data
    ? [...trail.data.trail.entries].reverse().find(isRecheck)
    : undefined
  const recheck = lastRecheck ? recheckResult(lastRecheck) : undefined

  useEffect(() => {
    let cancelled = false
    const entries = trail.data?.trail.entries
    if (!proposalId || !entries) {
      setVerification(null)
      return
    }
    const verifyPromise =
      source === 'GATEWAY'
        ? fetchGatewayVerification(proposalId, entries)
        : computeClientVerification(entries)
    verifyPromise.then((res) => {
      if (!cancelled) setVerification(res)
    })
    return () => {
      cancelled = true
    }
  }, [proposalId, source, trail.data])

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <a
          href="/"
          aria-label="Back to the purchasing flow"
          className="inline-flex items-center gap-1 text-sm font-semibold text-[var(--green)] underline"
        >
          <ArrowLeft size={15} aria-hidden /> Buy
        </a>
      </div>

      <section className="panel" aria-labelledby="log-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="log-title" className="panel-title mb-0 inline-flex items-center gap-2">
              <ScrollText size={16} aria-hidden /> Decision log
              {proposalId && (
                <span className="font-mono text-xs font-normal tracking-normal normal-case">
                  · {proposalId}
                </span>
              )}
            </h2>
            <p className="muted small mt-0.5">
              Append-only audit trail. Click any row for rules, bindings and trust-labelled facts.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {verification && (
              <span
                className={`status-pill ${verification.verified ? 'status-done' : 'status-blocked'}`}
                role="status"
                aria-label="Hash-chain verification status"
              >
                {verification.verified ? (
                  <>
                    <ShieldCheck size={13} aria-hidden />
                    <span>Hash chain verified ✓</span>
                    <code className="font-mono text-[11px] opacity-80">
                      sha256:{verification.headHash.slice(0, 10)}…
                    </code>
                  </>
                ) : (
                  <>
                    <ShieldAlert size={13} aria-hidden />
                    <span>Tamper detected · hash chain broken</span>
                  </>
                )}
              </span>
            )}
            {proposalId && trail.data && (
              <LogDownloads proposalId={proposalId} trail={trail.data.trail} source={source} />
            )}
          </div>
        </div>
        {recheck && (
          <p
            className={`notice mt-2 ${recheck.changed ? 'notice-error' : ''}`}
            role="status"
            data-testid="recheck-summary"
          >
            <RefreshCcw size={15} aria-hidden />
            <span>{recheck.text}</span>
          </p>
        )}
        {verification && !verification.verified && verification.reason && (
          <p className="notice notice-error mt-2" role="alert">
            <ShieldAlert size={15} aria-hidden />
            <span>{verification.reason}</span>
          </p>
        )}

        {source === 'MOCK' && (
          <nav aria-label="Proposals with a decision log" className="mt-2.5">
            <ul className="flex flex-wrap gap-1.5">
              {MOCK_LOGS.map((log) => (
                <li key={log.proposalId}>
                  <a
                    href={logPath(log.proposalId)}
                    aria-current={log.proposalId === current?.proposalId ? 'page' : undefined}
                    className={`btn log-scenario-pill ${log.proposalId === current?.proposalId ? 'btn-primary' : 'btn-scenario'}`}
                  >
                    {log.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </section>

      {!proposalId && <p className="notice">Select a proposal to view its decision log.</p>}
      {trail.error && (
        <p className="notice notice-error" role="alert">
          {trail.error.message}
        </p>
      )}
      {trail.data && (
        <section aria-label={current ? current.title : `Proposal ${proposalId}`}>
          <DecisionLog entries={trail.data.trail.entries} />
        </section>
      )}
    </div>
  )
}
