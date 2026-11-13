// The main view talks to the gateway only through this module.
// Until the agent endpoint is live, the data source is the synthetic mocks in src/mocks/.
import type { Mandate, Proposal } from './contracts'
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

export interface Gateway {
  source: DataSource
  listMandates(): Promise<Mandate[]>
  run(input: RunInput): Promise<FlowResult>
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

export class GatewayError extends Error {}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

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
        'Mock mode only knows scenarios A, B and C. Free-text requests need the live gateway.',
      )
    }
    if (match.customerId !== customerId) {
      const mandate = MOCK_MANDATES.find((m) => m.customerId === match.customerId)
      throw new GatewayError(
        `Scenario ${match.id} is recorded for ${mandate?.organisationName ?? match.customerId}. Pick that mandate to run it.`,
      )
    }
    return { proposal: MOCK_PROPOSALS[match.id], source: 'MOCK' }
  },
}

export const gateway: Gateway = mockGateway
