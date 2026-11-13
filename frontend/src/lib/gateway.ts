// The UI talks to the gateway only through this module, over two separate channels:
//   - `gateway`: the agent channel (run, read proposal state). It never carries an approval token.
//   - `approverChannel`: the approver channel (ApproverView with a single-use demo token, decideApproval).
// Until the live endpoints exist, both are backed by the synthetic mocks in src/mocks/. The mock keeps
// its state in localStorage, so the main view and the Approver panel in another tab see the same proposal.
import type { ApprovalAction, ApproverView, Mandate, Proposal } from './contracts'
import mandateM1 from '../mocks/mandate-m1.json'
import mandateM2 from '../mocks/mandate-m2.json'
import scenarioA from '../mocks/scenario-a.json'
import scenarioB from '../mocks/scenario-b.json'
import scenarioC from '../mocks/scenario-c.json'

export type DataSource = 'MOCK' | 'GATEWAY'

export type FlowResult = {
  proposal: Proposal
  source: DataSource
}

export type RunInput = {
  customerId: string
  requestText: string
}

/** Agent channel. */
export interface Gateway {
  source: DataSource
  listMandates(): Promise<Mandate[]>
  run(input: RunInput): Promise<FlowResult>
  getProposal(proposalId: string): Promise<Proposal>
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
  viewByToken(approvalToken: string): Promise<ApproverView & { loggedAt?: string }>
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

export class GatewayError extends Error {
  constructor(
    public code: GatewayErrorCode,
    message: string,
  ) {
    super(message)
  }
}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

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
  async listMandates() {
    return MOCK_MANDATES
  },
  async run({ customerId, requestText }) {
    await pause(250)
    const text = requestText.trim()
    const match = SCENARIOS.find((s) => s.requestText === text)
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
    const proposal: Proposal = {
      ...base,
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
    return { proposal, source: 'MOCK' }
  },
  async getProposal(proposalId) {
    return agentView(current(load(), proposalId))
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
    const approvalUrl = `${origin}/approve/${encodeURIComponent(record.approvalToken)}`
    return {
      proposal: record.proposal,
      approver: mandate.approver,
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
    const approvalUrl = `${origin}/approve/${encodeURIComponent(record.approvalToken)}`
    return {
      proposal: record.proposal,
      approver: mandate.approver,
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

export const gateway: Gateway = mockGateway
export const approverChannel: ApproverChannel = mockApproverChannel
