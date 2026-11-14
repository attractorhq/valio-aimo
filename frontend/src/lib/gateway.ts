// The UI talks to the gateway only through this module, over two separate channels:
//   - `gateway`: the agent channel (run, read proposal state). It never carries an approval token.
//   - `approverChannel`: the approver channel (ApproverView with a single-use demo token, decideApproval).
//
// In live/recorded mode, both talk to `/v0.1/*` on the same origin (proxied to :8000 in Vite dev).
// When the `/v0.1` gateway is unreachable, the UI falls back explicitly to the synthetic mocks in
// `src/mocks/`, labelled "Synthetic mock data". Mock and live results are never mixed silently.
import type {
  ApprovalAction,
  ApproverView,
  AuditTrail,
  Decision,
  ExcludedProduct,
  Mandate,
  Offer,
  Proposal,
  ReasonCode,
  ReasonOccurrence,
  StructuredRequest,
} from './contracts'
import mandateM1 from '../mocks/mandate-m1.json'
import mandateM2 from '../mocks/mandate-m2.json'
import scenarioA from '../mocks/scenario-a.json'
import scenarioB from '../mocks/scenario-b.json'
import scenarioC from '../mocks/scenario-c.json'
import { findMockLog, LogNotFound } from '../components/log/logData'
import { formatApprovalStatement } from '../components/approver/explain'

export type DataSource = 'MOCK' | 'GATEWAY'

export type GatewayHealth = {
  status: 'ok' | 'degraded'
  version?: string
  ready: boolean
  mode?: 'LIVE' | 'RECORDED'
  catalogProducts?: number
  reason?: string
}

export type ExplanationResult = {
  text: string
  sentenceCount: number
  reasons: ReasonOccurrence[]
  usedLlm: boolean
}

export type AgentStepEvent = {
  step: 1 | 2 | 3 | 4
  name: 'REQUEST' | 'LOOKUP' | 'ORDER' | 'APPROVAL' | 'ERROR'
  title: 'Request' | 'Lookup' | 'Order' | 'Approval' | 'Error'
  status: 'done' | 'waiting' | 'blocked'
  summary: string
  request?: StructuredRequest | null
  clarificationQuestion?: string | null
  plan?: unknown[] | null
  offer?: Offer | null
  proposal?: Proposal | null
  decision?: Decision | null
  explanation?: ExplanationResult | null
  reasons?: ReasonCode[]
  excluded?: ExcludedProduct[]
  error?: { code: string; message: string } | null
}

/** T44: on a hard-limit BLOCK, what would fit under the limit. Information only; never ordered. */
export type FeasibleOrder = {
  hardLimitEur: string
  totalExclVat: string
  text: string
}

export type FlowResult = {
  feasibleWithinHardLimit?: FeasibleOrder | null
  proposal?: Proposal | null
  steps: AgentStepEvent[]
  request?: StructuredRequest | null
  clarificationQuestion?: string | null
  offer?: Offer | null
  decision?: Decision | null
  explanation?: ExplanationResult | null
  reasons: ReasonCode[]
  excluded: ExcludedProduct[]
  error?: { code: string; message: string } | null
  mode?: 'LIVE' | 'RECORDED'
  source: DataSource
}

export type RunInput = {
  customerId: string
  requestText: string
  onStep?: (step: AgentStepEvent, stepsSoFar: AgentStepEvent[]) => void
}

/** Agent channel. */
export interface Gateway {
  readonly source: DataSource
  readonly healthMode?: 'LIVE' | 'RECORDED'
  checkHealth(): Promise<GatewayHealth | null>
  listMandates(): Promise<Mandate[]>
  run(input: RunInput): Promise<FlowResult>
  getProposal(proposalId: string): Promise<Proposal>
  getAuditTrail(proposalId: string): Promise<{ trail: AuditTrail; source: DataSource }>
}

/** A proposal as the approver sees it in the list; `loggedAt` is when the decision was logged. */
export type ApproverItem = {
  proposal: Proposal
  loggedAt?: string
}

export type DecideInput = {
  proposalId: string
  offerVersion: number
  approvalToken: string
  action: ApprovalAction
  comment?: string
}

/** Approver channel. Never used by the agent view. */
export interface ApproverChannel {
  listProposals(customerId: string): Promise<ApproverItem[]>
  view(proposalId: string): Promise<ApproverView & { loggedAt?: string }>
  viewByToken(
    approvalToken: string,
    proposalId?: string,
  ): Promise<ApproverView & { loggedAt?: string }>
  decide(input: DecideInput): Promise<ApproverItem>
}

export type ScenarioId = 'A' | 'B' | 'C'

export type Scenario = {
  id: ScenarioId
  title: string
  expected: string
  customerId: string
  requestText: string
}

// JSON imports widen string literals; the backend contract tests validate these files.
const MOCK_MANDATES = [mandateM1, mandateM2] as Mandate[]
const MOCK_PROPOSALS: Record<ScenarioId, Proposal> = {
  A: scenarioA as Proposal,
  B: scenarioB as Proposal,
  C: scenarioC as Proposal,
}

export const SCENARIOS: Scenario[] = (
  [
    ['A', 'Order on its own', 'Auto'],
    ['B', 'Ask at exactly the right point', 'Ask'],
    ['C', 'Refuse to break a hard limit', 'Block'],
  ] as const
).map(([id, title, expected]) => ({
  id,
  title,
  expected,
  customerId: MOCK_PROPOSALS[id].customerId,
  requestText: MOCK_PROPOSALS[id].request.requestText,
}))

/** Resolve the scenario's customerId against the active mandates list (handles both live and mock mandates). */
export function scenarioCustomerId(id: ScenarioId, mandates: Mandate[]): string {
  const scenario = SCENARIOS.find((s) => s.id === id)!
  if (mandates.some((m) => m.customerId === scenario.customerId)) {
    return scenario.customerId
  }
  if (id === 'B') {
    const strict = mandates.find((m) => m.allergenMode === 'STRICT')
    if (strict) return strict.customerId
  } else {
    const standard = mandates.find((m) => m.allergenMode === 'STANDARD')
    if (standard) return standard.customerId
  }
  return mandates[0]?.customerId ?? scenario.customerId
}

export type GatewayErrorCode =
  | 'UNKNOWN_REQUEST'
  | 'WRONG_MANDATE'
  | 'NOT_FOUND'
  | 'TOKEN_INVALID'
  | 'TOKEN_USED'
  | 'EXPIRED'
  | 'SUPERSEDED'
  | 'VERSION_MISMATCH'
  | 'NOT_PENDING'
  | 'UPSTREAM_UNAVAILABLE'
  | 'VALIO_RATE_LIMITED'
  | 'GATEWAY_ERROR'

export class GatewayError extends Error {
  constructor(
    public code: GatewayErrorCode,
    message: string,
  ) {
    super(message)
  }
}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function mockPlanForProposal(proposal: Proposal) {
  return proposal.request.items.map((item) => {
    const isMilk = item.what.toLowerCase().includes('milk')
    if (isMilk) {
      const fatPref = (item.preferences ?? []).find((p) => p.kind === 'FAT_PERCENT')
      const normFat = fatPref?.value.replace(',', '.').replace(/%/g, '').trim()
      const allCandidates = [
        {
          productId: 'MOCK-MILK',
          fat: '1.5',
        },
        {
          productId: 'MOCK-MILK-3PCT',
          fat: '3',
        },
      ]
      const candidates = normFat ? allCandidates.filter((c) => c.fat === normFat) : allCandidates
      return {
        item,
        shortlistIds: candidates.map((c) => c.productId),
      }
    }
    const chosen = proposal.lines.filter((l) => l.itemId === item.itemId)
    return {
      item,
      shortlistIds: chosen.map((l) => l.productId),
    }
  })
}

function stepsFromProposal(proposal: Proposal): AgentStepEvent[] {
  const n = proposal.request.items.length
  const approvalStatus: AgentStepEvent['status'] =
    proposal.decision === 'BLOCK'
      ? 'blocked'
      : proposal.state === 'CONFIRMED_SIMULATED'
        ? 'done'
        : ['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)
          ? 'blocked'
          : 'waiting'
  const approvalSummary =
    proposal.decision === 'BLOCK'
      ? 'Blocked by mandate'
      : proposal.state === 'CONFIRMED_SIMULATED'
        ? proposal.decision === 'ASK'
          ? `Approved by ${proposal.approverRole ?? 'the approver'} → Confirmed (simulated)`
          : 'Agent decided → Confirmed (simulated)'
        : 'Waiting for approver'

  return [
    {
      step: 1,
      name: 'REQUEST',
      title: 'Request',
      status: 'done',
      summary: `${n} item${n === 1 ? '' : 's'} · structured`,
      request: proposal.request,
      reasons: [],
      excluded: [],
    },
    {
      step: 2,
      name: 'LOOKUP',
      title: 'Lookup',
      status: 'done',
      summary: 'Live price & stock for the shortlist',
      request: proposal.request,
      plan: mockPlanForProposal(proposal),
      reasons: [],
      excluded: proposal.excluded ?? [],
    },
    {
      step: 3,
      name: 'ORDER',
      title: 'Order',
      status: 'done',
      summary: `Proposal at fixed price · offer v${proposal.offerVersion} · €${proposal.totals.exclVat} excl. VAT`,
      request: proposal.request,
      reasons: [],
      excluded: proposal.excluded ?? [],
    },
    {
      step: 4,
      name: 'APPROVAL',
      title: 'Approval',
      status: approvalStatus,
      summary: approvalSummary,
      request: proposal.request,
      proposal,
      decision: {
        decision: proposal.decision,
        reasons: proposal.reasons,
        approverRole: proposal.approverRole,
      },
      reasons: proposal.reasons,
      excluded: proposal.excluded ?? [],
    },
  ]
}

// ---- Mock server state ----------------------------------------------------------------------------

const OFFER_VALIDITY_MS = 15 * 60 * 1000
export const MOCK_STORE_KEY = 'verified-or-ask.mock-gateway.v1'

type MockRecord = {
  proposal: Proposal
  approvalToken?: string
  tokenUsed: boolean
  loggedAt?: string
}
type MockStore = Record<string, MockRecord>

let memoryStore: MockStore = {}

function load(): MockStore {
  try {
    const raw = localStorage.getItem(MOCK_STORE_KEY)
    return raw ? (JSON.parse(raw) as MockStore) : {}
  } catch {
    return memoryStore
  }
}

function save(store: MockStore) {
  memoryStore = store
  try {
    localStorage.setItem(MOCK_STORE_KEY, JSON.stringify(store))
  } catch {
    // Private mode or blocked storage: state stays in this tab only.
  }
}

function randomId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  }
}

/** Lazily expire pending proposals whose offer validity passed (P1.4: an expired proposal → EXPIRED). */
function current(store: MockStore, proposalId: string): MockRecord {
  const record = store[proposalId]
  if (!record) throw new GatewayError('NOT_FOUND', 'This proposal does not exist.')
  const { proposal } = record
  if (proposal.state === 'PENDING_APPROVAL' && Date.parse(proposal.validUntil) < Date.now()) {
    record.proposal = { ...proposal, state: 'EXPIRED' }
    save(store)
  }
  return record
}

/** Agent-facing copy: the record's token never leaves the approver channel. */
function agentView(record: MockRecord): Proposal {
  return record.proposal
}

export const mockGateway: Gateway = {
  source: 'MOCK',
  healthMode: 'RECORDED',
  async checkHealth() {
    return null
  },
  async listMandates() {
    return MOCK_MANDATES
  },
  async run({ customerId, requestText, onStep }) {
    await pause(250)
    const text = requestText.trim()
    const stripped = text.replace(/\s*\([^()]+\)(?=\s*(?:[.;!\n]|$))/, '')
    const qualifierMatch = text.match(/\(([^()]+)\)(?=\s*(?:[.;!\n]|$))/)
    const qualifier = qualifierMatch?.[1]?.trim()
    const match = SCENARIOS.find((s) => s.requestText === text || s.requestText === stripped)
    if (!match) {
      throw new GatewayError(
        'UNKNOWN_REQUEST',
        'Mock mode only knows scenarios A, B and C. Free-text requests need the live gateway.',
      )
    }
    if (match.customerId !== customerId) {
      const mandate = MOCK_MANDATES.find((m) => m.customerId === match.customerId)
      throw new GatewayError(
        'WRONG_MANDATE',
        `Scenario ${match.id} is recorded for ${mandate?.organisationName ?? match.customerId}. Pick that mandate to run it.`,
      )
    }
    // Each run is a fresh proposal, valid for 15 minutes from now (the recorded facts stay as recorded).
    const base = MOCK_PROPOSALS[match.id]
    const now = Date.now()
    let updatedRequest = base.request
    if (qualifier) {
      const fatMatch = qualifier.match(/(\d+(?:[.,]\d+)?)\s*%/)
      const fatValue = fatMatch ? fatMatch[1].replace(',', '.') : undefined
      updatedRequest = {
        ...base.request,
        requestText: text,
        items: base.request.items.map((item, idx) =>
          idx === 0
            ? {
                ...item,
                preferences: fatValue
                  ? [
                      ...(item.preferences ?? []).filter((p) => p.kind !== 'FAT_PERCENT'),
                      { kind: 'FAT_PERCENT' as const, value: fatValue },
                    ]
                  : [...(item.preferences ?? []), { kind: 'OTHER' as const, value: qualifier }],
              }
            : item,
        ),
        assumptions: fatValue
          ? (base.request.assumptions ?? []).filter((a) => !/any\s+fat/i.test(a))
          : [...(base.request.assumptions ?? []), qualifier],
      }
    }
    const proposal: Proposal = {
      ...base,
      request: updatedRequest,
      proposalId: `${base.proposalId}-${randomId().slice(0, 8)}`,
      createdAt: new Date(now).toISOString(),
      validUntil: new Date(now + OFFER_VALIDITY_MS).toISOString(),
    }
    const store = load()
    store[proposal.proposalId] = {
      proposal,
      approvalToken:
        proposal.state === 'PENDING_APPROVAL'
          ? `tk-${match.id}-${proposal.proposalId}-${randomId().slice(0, 8)}`
          : undefined,
      tokenUsed: false,
    }
    save(store)
    const steps = stepsFromProposal(proposal)
    if (onStep) {
      const acc: AgentStepEvent[] = []
      for (const s of steps) {
        acc.push(s)
        onStep(s, [...acc])
      }
    }
    return {
      proposal,
      steps,
      request: proposal.request,
      reasons: proposal.reasons,
      excluded: proposal.excluded ?? [],
      mode: proposal.mode,
      source: 'MOCK',
    }
  },
  async getProposal(proposalId) {
    return agentView(current(load(), proposalId))
  },
  async getAuditTrail(proposalId) {
    const found = findMockLog(proposalId)
    if (!found) throw new LogNotFound(`No decision log for proposal ${proposalId}.`)
    return { trail: found.trail, source: 'MOCK' }
  },
}

export const mockApproverChannel: ApproverChannel = {
  async listProposals(customerId) {
    const store = load()
    return Object.keys(store)
      .map((id) => current(store, id))
      .filter((r) => r.proposal.customerId === customerId && r.proposal.decision === 'ASK')
      .sort((a, b) => b.proposal.createdAt.localeCompare(a.proposal.createdAt))
      .map(({ proposal, loggedAt }) => ({ proposal, loggedAt }))
  },
  async view(proposalId) {
    const record = current(load(), proposalId)
    const mandate = MOCK_MANDATES.find((m) => m.customerId === record.proposal.customerId)
    if (!mandate || !record.approvalToken) {
      throw new GatewayError('NOT_PENDING', 'This proposal never needed an approval.')
    }
    const origin = typeof window !== 'undefined' ? window.location.origin : ''
    const approvalUrl = `${origin}/approve/${encodeURIComponent(record.approvalToken)}?proposalId=${encodeURIComponent(record.proposal.proposalId)}`
    return {
      proposal: record.proposal,
      approver: mandate.approver,
      approvalStatement: formatApprovalStatement(record.proposal, mandate),
      approvalToken: record.approvalToken,
      approvalUrl,
      qrPayload: approvalUrl,
      expiresAt: record.proposal.validUntil,
      permissionNotice: 'Demo approval link — single use',
      loggedAt: record.loggedAt,
    }
  },
  async viewByToken(approvalToken) {
    const store = load()
    let foundId = Object.keys(store).find((id) => store[id]?.approvalToken === approvalToken)
    if (!foundId) {
      // Cross-device fallback for a real phone on mobile data scanning a demo QR token in mock mode:
      const m = approvalToken.match(/^tk-([ABC])-(MOCK-P-[ABC]-[0-9a-f]{8})-.+$/)
      if (m) {
        const scenarioId = m[1] as ScenarioId
        const proposalId = m[2]
        const base = MOCK_PROPOSALS[scenarioId]
        const now = Date.now()
        store[proposalId] = {
          proposal: {
            ...base,
            proposalId,
            createdAt: new Date(now).toISOString(),
            validUntil: new Date(now + OFFER_VALIDITY_MS).toISOString(),
          },
          approvalToken,
          tokenUsed: false,
        }
        save(store)
        foundId = proposalId
      }
    }
    if (!foundId) {
      throw new GatewayError('TOKEN_INVALID', 'This approval link is not valid for any proposal.')
    }
    const record = current(store, foundId)
    const mandate = MOCK_MANDATES.find((m) => m.customerId === record.proposal.customerId)
    if (!mandate || !record.approvalToken) {
      throw new GatewayError('NOT_PENDING', 'This proposal never needed an approval.')
    }
    const origin = typeof window !== 'undefined' ? window.location.origin : ''
    const approvalUrl = `${origin}/approve/${encodeURIComponent(record.approvalToken)}?proposalId=${encodeURIComponent(record.proposal.proposalId)}`
    return {
      proposal: record.proposal,
      approver: mandate.approver,
      approvalStatement: formatApprovalStatement(record.proposal, mandate),
      approvalToken: record.approvalToken,
      approvalUrl,
      qrPayload: approvalUrl,
      expiresAt: record.proposal.validUntil,
      permissionNotice: 'Demo approval link — single use',
      loggedAt: record.loggedAt,
    }
  },
  async decide({ proposalId, offerVersion, approvalToken, action }) {
    await pause(300)
    const store = load()
    const record = current(store, proposalId)
    const { proposal } = record
    if (!record.approvalToken || approvalToken !== record.approvalToken) {
      throw new GatewayError('TOKEN_INVALID', 'This approval link is not valid for this proposal.')
    }
    if (record.tokenUsed) {
      throw new GatewayError(
        'TOKEN_USED',
        'This approval link was already used. Each demo link works once.',
      )
    }
    if (proposal.state === 'SUPERSEDED') {
      throw new GatewayError(
        'SUPERSEDED',
        'This version was superseded. A new version was decided from scratch; this approval is void.',
      )
    }
    if (proposal.state === 'EXPIRED') {
      throw new GatewayError('EXPIRED', 'The offer expired before sign-off. It must be re-quoted.')
    }
    if (offerVersion !== proposal.offerVersion) {
      throw new GatewayError(
        'VERSION_MISMATCH',
        `You saw version ${offerVersion}, but the proposal is now version ${proposal.offerVersion}.`,
      )
    }
    if (proposal.state !== 'PENDING_APPROVAL') {
      throw new GatewayError('NOT_PENDING', 'This proposal is no longer waiting for approval.')
    }
    record.tokenUsed = true
    record.loggedAt = new Date().toISOString()
    // Approval triggers the server re-check, then the simulated confirmation. The mock's recorded
    // facts cannot change, so its re-check always passes; the live gateway can return SUPERSEDED here.
    record.proposal = {
      ...proposal,
      state: action === 'APPROVE' ? 'CONFIRMED_SIMULATED' : 'REJECTED',
    }
    save(store)
    return { proposal: record.proposal, loggedAt: record.loggedAt }
  },
}

// ---- Live `/v0.1` gateway implementation with explicit mock fallback ------------------------------

let activeSource: DataSource = 'MOCK'
let activeHealthMode: 'LIVE' | 'RECORDED' | undefined
let probePromise: Promise<DataSource> | null = null
let onSourceChanged: ((source: DataSource) => void) | null = null

export function subscribeGatewaySource(cb: (source: DataSource) => void): () => void {
  onSourceChanged = cb
  return () => {
    if (onSourceChanged === cb) onSourceChanged = null
  }
}

function setSource(next: DataSource) {
  if (activeSource !== next) {
    activeSource = next
    onSourceChanged?.(next)
  } else {
    activeSource = next
  }
}

const TOKEN_PROPOSAL_MAP_KEY = 'verified-or-ask.token-proposal-map.v1'

function rememberTokenProposal(token: string, proposalId: string): void {
  try {
    const raw = localStorage.getItem(TOKEN_PROPOSAL_MAP_KEY)
    const map: Record<string, string> = raw ? (JSON.parse(raw) as Record<string, string>) : {}
    map[token] = proposalId
    localStorage.setItem(TOKEN_PROPOSAL_MAP_KEY, JSON.stringify(map))
  } catch {
    // ignore storage errors
  }
}

function lookupProposalForToken(token: string): string | undefined {
  try {
    const raw = localStorage.getItem(TOKEN_PROPOSAL_MAP_KEY)
    if (!raw) return undefined
    const map = JSON.parse(raw) as Record<string, string>
    return map[token]
  } catch {
    return undefined
  }
}

function mapBackendError(code: string | undefined, message: string, status: number): GatewayError {
  switch (code) {
    case 'TOKEN_ALREADY_USED':
      return new GatewayError(
        'TOKEN_USED',
        'This approval link was already used. Each demo link works once.',
      )
    case 'APPROVAL_TOKEN_INVALID':
      return new GatewayError('TOKEN_INVALID', 'This approval link is not valid for this proposal.')
    case 'APPROVAL_EXPIRED':
      return new GatewayError('EXPIRED', 'The offer expired before sign-off. It must be re-quoted.')
    case 'PROPOSAL_SUPERSEDED':
      return new GatewayError(
        'SUPERSEDED',
        'This version was superseded. A new version was decided from scratch; this approval is void.',
      )
    case 'NOT_APPROVABLE':
    case 'INVALID_STATE':
      return new GatewayError(
        'NOT_PENDING',
        message || 'This proposal is no longer waiting for approval.',
      )
    case 'NOT_FOUND':
      return new GatewayError('NOT_FOUND', message || 'This proposal does not exist.')
    case 'UPSTREAM_ERROR':
    case 'UPSTREAM_UNAVAILABLE':
      return new GatewayError(
        'UPSTREAM_UNAVAILABLE',
        message ||
          'Live price/availability could not be read for this shortlist; nothing was ordered.',
      )
    case 'VALIO_RATE_LIMITED':
      return new GatewayError(
        'VALIO_RATE_LIMITED',
        message || 'Valio API rate limit: retry in 30 s',
      )
    default:
      if (status === 404) {
        return new GatewayError('NOT_FOUND', message || 'Not found.')
      }
      return new GatewayError('GATEWAY_ERROR', message || `Gateway error (HTTP ${status}).`)
  }
}

async function parseErrorResponse(res: Response): Promise<GatewayError> {
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string }
      detail?: string
    }
    const code = body.error?.code
    const msg = body.error?.message || body.detail || res.statusText
    return mapBackendError(code, msg, res.status)
  } catch {
    return mapBackendError(undefined, res.statusText, res.status)
  }
}

async function ensureSource(): Promise<DataSource> {
  if (probePromise) return probePromise
  probePromise = (async () => {
    if (import.meta.env.VITE_API_BASE_URL === 'off') {
      setSource('MOCK')
      return 'MOCK'
    }
    try {
      const res = await fetch('/health', {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(2000),
      })
      if (!res.ok) {
        setSource('MOCK')
        return 'MOCK'
      }
      const ct = (res.headers.get('content-type') || '').toLowerCase()
      if (!ct.includes('application/json')) {
        setSource('MOCK')
        return 'MOCK'
      }
      const data = (await res.json()) as GatewayHealth
      if (data && data.ready) {
        activeHealthMode = data.mode
        setSource('GATEWAY')
        return 'GATEWAY'
      }
      setSource('MOCK')
      return 'MOCK'
    } catch {
      setSource('MOCK')
      return 'MOCK'
    }
  })()
  return probePromise
}

function isMockId(id: string): boolean {
  return id.startsWith('MOCK-') || id.startsWith('tk-')
}

export const gateway: Gateway = {
  get source(): DataSource {
    return activeSource
  },
  get healthMode(): 'LIVE' | 'RECORDED' | undefined {
    return activeHealthMode
  },
  async checkHealth() {
    if (import.meta.env.VITE_API_BASE_URL === 'off') {
      setSource('MOCK')
      return null
    }
    try {
      const res = await fetch('/health', {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(2000),
      })
      if (!res.ok) {
        setSource('MOCK')
        return null
      }
      const ct = (res.headers.get('content-type') || '').toLowerCase()
      if (!ct.includes('application/json')) {
        setSource('MOCK')
        return null
      }
      const data = (await res.json()) as GatewayHealth
      if (data.ready) {
        activeHealthMode = data.mode
        setSource('GATEWAY')
      } else {
        setSource('MOCK')
      }
      return data
    } catch {
      setSource('MOCK')
      return null
    }
  },
  async listMandates() {
    const src = await ensureSource()
    if (src === 'MOCK') return mockGateway.listMandates()
    try {
      const res = await fetch('/v0.1/mandates', {
        headers: { Accept: 'application/json' },
      })
      if (!res.ok) {
        if (res.status === 502 || res.status === 503 || res.status === 504) {
          setSource('MOCK')
          return mockGateway.listMandates()
        }
        throw await parseErrorResponse(res)
      }
      const mandates = (await res.json()) as Mandate[]
      setSource('GATEWAY')
      if (!activeHealthMode) {
        void this.checkHealth()
      }
      return mandates
    } catch (err) {
      if (err instanceof GatewayError) throw err
      setSource('MOCK')
      return mockGateway.listMandates()
    }
  },
  async run(input) {
    const src = await ensureSource()
    if (src === 'MOCK') {
      return mockGateway.run(input)
    }

    const res = await fetch('/v0.1/agent/run', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/x-ndjson',
      },
      body: JSON.stringify({
        customerId: input.customerId,
        requestText: input.requestText,
      }),
    })

    if (!res.ok) {
      throw await parseErrorResponse(res)
    }

    const steps: AgentStepEvent[] = []
    let finalPayload: Record<string, unknown> | null = null
    let errorStep: AgentStepEvent | null = null

    const contentType = (res.headers.get('content-type') || '').toLowerCase()
    if (contentType.includes('application/x-ndjson') && res.body) {
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const processLine = (line: string) => {
        const trimmed = line.trim()
        if (!trimmed) return
        const parsed = JSON.parse(trimmed) as Record<string, unknown>
        const rowType = parsed.type as string | undefined
        if (rowType === 'step') {
          const stepEv = parsed as unknown as AgentStepEvent
          steps.push(stepEv)
          input.onStep?.(stepEv, [...steps])
        } else if (rowType === 'error') {
          const stepEv = parsed as unknown as AgentStepEvent
          errorStep = stepEv
          steps.push(stepEv)
          input.onStep?.(stepEv, [...steps])
        } else if (rowType === 'result') {
          finalPayload = parsed
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          processLine(line)
        }
      }
      buffer += decoder.decode()
      if (buffer.trim()) {
        for (const line of buffer.split('\n')) {
          processLine(line)
        }
      }
    } else {
      // Fallback if response was plain JSON
      finalPayload = (await res.json()) as Record<string, unknown>
      const rawSteps = (finalPayload.steps as AgentStepEvent[] | undefined) ?? []
      for (const s of rawSteps) {
        steps.push(s)
        input.onStep?.(s, [...steps])
      }
    }

    if (errorStep) {
      const errObj = (errorStep as AgentStepEvent).error ?? {
        code: 'UPSTREAM_UNAVAILABLE',
        message:
          (errorStep as AgentStepEvent).summary ||
          'Live price/availability could not be read for this shortlist; nothing was ordered.',
      }
      const step1 = steps.find((s) => s.step === 1)
      const step2 = steps.find((s) => s.step === 2 && s.name === 'LOOKUP')
      return {
        proposal: null,
        steps,
        request: step2?.request ?? step1?.request ?? null,
        clarificationQuestion: null,
        offer: null,
        decision: null,
        explanation: null,
        reasons: [],
        excluded: step2?.excluded ?? [],
        error: errObj,
        mode: activeHealthMode,
        source: 'GATEWAY',
      }
    }

    const lastStep = steps[steps.length - 1]
    const proposal =
      ((finalPayload?.proposal as Proposal | null | undefined) ?? lastStep?.proposal) || null
    const request =
      ((finalPayload?.request as StructuredRequest | null | undefined) ??
        lastStep?.request ??
        steps[0]?.request) ||
      null
    const clarificationQuestion =
      ((finalPayload?.clarificationQuestion as string | null | undefined) ??
        lastStep?.clarificationQuestion) ||
      null
    const offer =
      ((finalPayload?.offer as Offer | null | undefined) ??
        lastStep?.offer ??
        steps.find((s) => s.step === 3)?.offer) ||
      null
    const decision =
      ((finalPayload?.decision as Decision | null | undefined) ?? lastStep?.decision) || null
    const explanation =
      ((finalPayload?.explanation as ExplanationResult | null | undefined) ??
        lastStep?.explanation) ||
      null
    const reasons = ((finalPayload?.reasons as ReasonCode[] | undefined) ?? lastStep?.reasons) || []
    const excluded =
      proposal?.excluded ?? lastStep?.excluded ?? steps.find((s) => s.step === 2)?.excluded ?? []
    const mode =
      proposal?.mode ??
      offer?.mode ??
      (finalPayload?.mode as 'LIVE' | 'RECORDED' | undefined) ??
      activeHealthMode

    if (mode) {
      activeHealthMode = mode
    }

    const feasibleWithinHardLimit =
      (finalPayload?.feasibleWithinHardLimit as FeasibleOrder | null | undefined) ?? null

    return {
      feasibleWithinHardLimit,
      proposal,
      steps,
      request,
      clarificationQuestion,
      offer,
      decision,
      explanation,
      reasons,
      excluded,
      error: null,
      mode,
      source: 'GATEWAY',
    }
  },
  async getProposal(proposalId) {
    if (isMockId(proposalId)) {
      return mockGateway.getProposal(proposalId)
    }
    const src = await ensureSource()
    if (src === 'MOCK') return mockGateway.getProposal(proposalId)
    const res = await fetch(`/v0.1/proposals/${encodeURIComponent(proposalId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) throw await parseErrorResponse(res)
    return (await res.json()) as Proposal
  },
  async getAuditTrail(proposalId) {
    if (isMockId(proposalId)) {
      return mockGateway.getAuditTrail(proposalId)
    }
    const src = await ensureSource()
    if (src === 'MOCK') {
      return mockGateway.getAuditTrail(proposalId)
    }
    const res = await fetch(`/v0.1/audit?proposalId=${encodeURIComponent(proposalId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) {
      if (res.status === 404) {
        throw new LogNotFound(`No decision log for proposal ${proposalId}.`)
      }
      throw await parseErrorResponse(res)
    }
    const trail = (await res.json()) as AuditTrail
    if (!trail.entries || trail.entries.length === 0) {
      throw new LogNotFound(`No decision log for proposal ${proposalId}.`)
    }
    return { trail, source: 'GATEWAY' }
  },
}

export const approverChannel: ApproverChannel = {
  async listProposals(customerId) {
    const src = await ensureSource()
    if (src === 'MOCK') return mockApproverChannel.listProposals(customerId)
    const res = await fetch(
      `/v0.1/approver/proposals?customerId=${encodeURIComponent(customerId)}`,
      { headers: { Accept: 'application/json' } },
    )
    if (!res.ok) throw await parseErrorResponse(res)
    const proposals = (await res.json()) as Proposal[]
    return proposals.filter((p) => p.decision === 'ASK').map((proposal) => ({ proposal }))
  },
  async view(proposalId) {
    if (isMockId(proposalId)) {
      return mockApproverChannel.view(proposalId)
    }
    const src = await ensureSource()
    if (src === 'MOCK') return mockApproverChannel.view(proposalId)
    const res = await fetch(`/v0.1/approver/proposals/${encodeURIComponent(proposalId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) throw await parseErrorResponse(res)
    const viewData = (await res.json()) as ApproverView
    rememberTokenProposal(viewData.approvalToken, viewData.proposal.proposalId)
    const origin = typeof window !== 'undefined' ? window.location.origin : ''
    const approvalUrl =
      viewData.approvalUrl ??
      `${origin}/approve/${encodeURIComponent(viewData.approvalToken)}?proposalId=${encodeURIComponent(viewData.proposal.proposalId)}`
    return {
      ...viewData,
      approvalUrl,
      qrPayload: viewData.qrPayload ?? approvalUrl,
    }
  },
  async viewByToken(approvalToken, proposalIdHint) {
    if (isMockId(approvalToken) || (proposalIdHint && isMockId(proposalIdHint))) {
      return mockApproverChannel.viewByToken(approvalToken, proposalIdHint)
    }
    const src = await ensureSource()
    if (src === 'MOCK') return mockApproverChannel.viewByToken(approvalToken, proposalIdHint)

    let targetProposalId = proposalIdHint || lookupProposalForToken(approvalToken)
    if (!targetProposalId) {
      // Discover the pending proposal whose issued token matches `approvalToken`
      const listRes = await fetch('/v0.1/approver/proposals?pendingOnly=true', {
        headers: { Accept: 'application/json' },
      })
      if (listRes.ok) {
        const pending = (await listRes.json()) as Proposal[]
        for (const p of pending) {
          const vRes = await fetch(`/v0.1/approver/proposals/${encodeURIComponent(p.proposalId)}`, {
            headers: { Accept: 'application/json' },
          })
          if (vRes.ok) {
            const vData = (await vRes.json()) as ApproverView
            rememberTokenProposal(vData.approvalToken, vData.proposal.proposalId)
            if (vData.approvalToken === approvalToken) {
              const origin = typeof window !== 'undefined' ? window.location.origin : ''
              const approvalUrl =
                vData.approvalUrl ??
                `${origin}/approve/${encodeURIComponent(vData.approvalToken)}?proposalId=${encodeURIComponent(vData.proposal.proposalId)}`
              return {
                ...vData,
                approvalUrl,
                qrPayload: vData.qrPayload ?? approvalUrl,
              }
            }
          }
        }
      }
      throw new GatewayError('TOKEN_INVALID', 'This approval link is not valid for any proposal.')
    }

    const vRes = await fetch(`/v0.1/approver/proposals/${encodeURIComponent(targetProposalId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (!vRes.ok) {
      // 1a. If the proposal is already decided/superseded on the server, return its current state
      if (vRes.status === 409) {
        const pRes = await fetch(`/v0.1/proposals/${encodeURIComponent(targetProposalId)}`, {
          headers: { Accept: 'application/json' },
        })
        if (pRes.ok) {
          const prop = (await pRes.json()) as Proposal
          return {
            proposal: prop,
            approver: {
              name: prop.approverRole ?? 'Head Chef (demo)',
              role: prop.approverRole ?? 'Head Chef (demo)',
            },
            approvalToken,
            expiresAt: prop.validUntil,
            permissionNotice: 'Demo approval link — single use',
          }
        }
      }
      throw await parseErrorResponse(vRes)
    }
    const viewData = (await vRes.json()) as ApproverView
    if (viewData.approvalToken !== approvalToken) {
      throw new GatewayError('TOKEN_INVALID', 'This approval link is not valid for this proposal.')
    }
    rememberTokenProposal(viewData.approvalToken, viewData.proposal.proposalId)
    const origin = typeof window !== 'undefined' ? window.location.origin : ''
    const approvalUrl =
      viewData.approvalUrl ??
      `${origin}/approve/${encodeURIComponent(viewData.approvalToken)}?proposalId=${encodeURIComponent(viewData.proposal.proposalId)}`
    return {
      ...viewData,
      approvalUrl,
      qrPayload: viewData.qrPayload ?? approvalUrl,
    }
  },
  async decide({ proposalId, offerVersion, approvalToken, action, comment }) {
    if (isMockId(proposalId) || isMockId(approvalToken)) {
      return mockApproverChannel.decide({
        proposalId,
        offerVersion,
        approvalToken,
        action,
        comment,
      })
    }
    const src = await ensureSource()
    if (src === 'MOCK') {
      return mockApproverChannel.decide({
        proposalId,
        offerVersion,
        approvalToken,
        action,
        comment,
      })
    }
    const res = await fetch(`/v0.1/approver/proposals/${encodeURIComponent(proposalId)}/decision`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({
        offerVersion,
        approvalToken,
        action,
        comment: comment || null,
      }),
    })
    if (!res.ok) {
      throw await parseErrorResponse(res)
    }
    const decidedProposal = (await res.json()) as Proposal
    return {
      proposal: decidedProposal,
      loggedAt: new Date().toISOString(),
    }
  },
}
