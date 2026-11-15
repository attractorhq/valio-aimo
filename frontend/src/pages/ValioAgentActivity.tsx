import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Mandate, Offer, Proposal } from '../lib/contracts'
import { adminHeaders, agentHeaders, DEMO_ADMIN_STORAGE } from '../lib/demoAccess'
import { formatTime } from '../lib/format'
import { gateway, scenarioCustomerId } from '../lib/gateway'
import scenarioC from '../mocks/scenario-c.json'

type Entry = {
  id: number
  timestamp: string
  client: string
  operation: string
  access: 'READ' | 'WRITE'
  outcome: 'OK' | 'REFUSED' | 'ERROR'
  durationMs: number
  reason: string | null
}

type AgentActivity = {
  paused: boolean
  suspended: Array<{ clientId: string; client: string; customerId: string; until?: string | null }>
  entries: Entry[]
}

type ActivityChange = { paused: boolean } | { clientId: string }

const ACTIVITY_URL = '/v0.1/admin/agent-activity'
const ACTIVITY_KEY = ['agent-activity']
const DEMO_CLIENT = 'Oversight demo'
const OUTCOMES = { OK: 'OK', REFUSED: 'Refused', ERROR: 'Error' }

async function activityRequest(change?: ActivityChange): Promise<AgentActivity> {
  const resume = change && 'clientId' in change
  const response = await fetch(resume ? `${ACTIVITY_URL}/resume` : ACTIVITY_URL, {
    method: resume ? 'POST' : change ? 'PATCH' : 'GET',
    headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
    body: change ? JSON.stringify(change) : undefined,
    signal: AbortSignal.timeout(10000),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error?.message ?? 'Could not load agent activity.')
  return body
}

type McpError = { code: string | number; message: string }
type ToolReply<T> = { output: T } | { error: McpError }
type RpcBody<T> = {
  result?: { isError?: boolean; structuredContent?: T & { error?: McpError } }
  error?: McpError
}

function mcpClient(customerId: string) {
  let sessionId: string | null = null
  let id = 0
  async function rpc<T>(method: string, params: object): Promise<RpcBody<T>> {
    const response = await fetch('/mcp', {
      method: 'POST',
      headers: {
        ...agentHeaders(customerId),
        'Content-Type': 'application/json',
        Accept: 'application/json',
        ...(sessionId ? { 'Mcp-Session-Id': sessionId } : {}),
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }),
      signal: AbortSignal.timeout(25000),
    })
    sessionId = response.headers.get('Mcp-Session-Id') ?? sessionId
    if (!response.ok) throw new Error('Could not reach the MCP gateway.')
    return response.json()
  }
  return {
    async initialize() {
      const result = await rpc('initialize', { clientInfo: { name: DEMO_CLIENT, version: '1' } })
      if (result.error || !sessionId) throw new Error('Could not start the demo agent session.')
    },
    async call<T>(name: string, args: object = {}): Promise<ToolReply<T>> {
      const reply = await rpc<T>('tools/call', { name, arguments: args })
      const content = reply.result?.structuredContent
      if (reply.error) return { error: reply.error }
      if (reply.result?.isError && content?.error) return { error: content.error }
      if (!content) throw new Error('The MCP gateway returned no tool result.')
      return { output: content }
    },
  }
}

async function runOversightDemo(refresh: () => Promise<AgentActivity | undefined>) {
  const customerId = scenarioCustomerId('C', await gateway.listMandates())
  const client = mcpClient(customerId)
  await client.initialize()

  const approval = await client.call('decideApproval')
  await refresh()
  if (!('error' in approval) || approval.error.code !== -32601) {
    throw new Error('The gateway did not refuse the demo approval operation as expected.')
  }

  // The reference request is only submitted when the server quote exceeds the current hard limit.
  const mandate = await client.call<Mandate>('getMandate')
  if ('error' in mandate) throw new Error(mandate.error.message)
  const sample = scenarioC as Proposal
  const item = { ...sample.request.items[0], productId: '464306' }
  const offer = await client.call<Offer>('getOffer', {
    requestedDate: sample.request.requestedDate,
    items: [{ itemId: item.itemId, productId: item.productId, quantity: item.quantity, unit: item.unit }],
  })
  if ('error' in offer) throw new Error(offer.error.message)
  if (!(Number(offer.output.totals.exclVat) > Number(mandate.output.hardLimitEur))) {
    throw new Error('The reference quote is below the current hard limit. The demo stopped before proposing it.')
  }
  const proposal = {
    offerId: offer.output.offerId,
    offerVersion: offer.output.offerVersion,
    request: { ...sample.request, items: [item] },
  }
  const decision = await client.call<Proposal>('createProposal', proposal)
  await refresh()
  if ('error' in decision) throw new Error(decision.error.message)
  if (decision.output.decision !== 'BLOCK' || !decision.output.reasons.includes('INSTRUCTION_IGNORED')) {
    throw new Error('The gateway did not report the ignored instruction in the blocked reference order.')
  }

  const extraField = await client.call('createProposal', { ...proposal, approvalToken: 'invalid-demo-value' })
  const activity = await refresh()
  if (!('error' in extraField) || extraField.error.code !== 'VALIDATION_ERROR') {
    throw new Error('The gateway did not reject the extra approval field as expected.')
  }
  if (!activity?.suspended.some((entry) => entry.client === DEMO_CLIENT && entry.customerId === customerId)) {
    throw new Error('The demo client is still active.')
  }
}

export function ValioAgentActivity() {
  const queryClient = useQueryClient()
  const [signedIn, setSignedIn] = useState(() => Boolean(sessionStorage.getItem(DEMO_ADMIN_STORAGE)))
  const activity = useQuery({
    queryKey: ACTIVITY_KEY,
    queryFn: () => activityRequest(),
    enabled: signedIn,
    refetchInterval: 5000,
    retry: false,
  })
  const change = useMutation({
    mutationFn: activityRequest,
    onMutate: () => queryClient.cancelQueries({ queryKey: ACTIVITY_KEY }),
    onSuccess: (data) => queryClient.setQueryData(ACTIVITY_KEY, data),
  })
  const demo = useMutation({
    mutationFn: () => runOversightDemo(async () => (await activity.refetch()).data),
    onSettled: () => activity.refetch(),
  })
  const busy = change.isPending || demo.isPending
  const error = (demo.error ?? change.error ?? activity.error)?.message
  const data = activity.data

  return (
    <section className="panel min-w-0" aria-labelledby="agent-activity-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="agent-activity-title" className="panel-title mb-0">Agent activity</h2>
        {!signedIn && (
          <button
            type="button"
            className="btn btn-scenario"
            onClick={() => {
              sessionStorage.setItem(DEMO_ADMIN_STORAGE, 'demo-admin')
              setSignedIn(true)
            }}
          >
            Enter admin key
          </button>
        )}
        {data && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="btn btn-scenario"
              aria-pressed={data.paused}
              disabled={busy}
              onClick={() => change.mutate({ paused: !data.paused })}
            >
              Pause agent writes
            </button>
            <button
              type="button"
              className="btn btn-scenario"
              disabled={busy || data.paused || data.suspended.some((client) => client.client === DEMO_CLIENT)}
              onClick={() => demo.mutate()}
            >
              {demo.isPending ? 'Running demo…' : 'Run rogue-agent demo'}
            </button>
          </div>
        )}
      </div>
      {signedIn && activity.isPending && <p role="status" className="muted small mt-2">Loading agent activity…</p>}
      {error && <p role="alert" className="notice notice-error mt-2">{error}</p>}
      {data && (
        <>
          <p role="status" className="muted small mt-2">
            {data.paused ? 'Agent writes paused. Reads are available.' : 'Agent writes enabled.'}
          </p>
          {data.suspended.length > 0 && (
            <ul className="mt-2 grid gap-1" aria-label="Suspended agents">
              {data.suspended.map((client) => (
                <li key={client.clientId} className="flex flex-wrap items-center gap-2 text-sm">
                  <strong className="break-words">
                    {client.client} suspended
                    {client.until && (
                      <>
                        {' '}until <time dateTime={client.until}>{formatTime(client.until)}</time>
                      </>
                    )}
                  </strong>
                  <button
                    type="button"
                    className="disclosure-btn"
                    aria-label={`Resume ${client.client} (${client.customerId})`}
                    disabled={busy}
                    onClick={() => change.mutate({ clientId: client.clientId })}
                  >
                    Resume
                  </button>
                </li>
              ))}
            </ul>
          )}
          {data.entries.length === 0 ? (
            <p className="muted small mt-2">No agent calls in this session.</p>
          ) : (
            <ol
              className="mt-2 max-h-80 overflow-y-auto divide-y divide-[var(--line)]"
              aria-label="Latest agent activity"
              tabIndex={0}
            >
              {data.entries.map((entry) => (
                <li key={entry.id} className="py-2 text-xs [overflow-wrap:anywhere]">
                  <span className={`font-semibold ${entry.outcome === 'REFUSED' ? 'text-[var(--block)]' : ''}`}>
                    {OUTCOMES[entry.outcome]}
                  </span>{' '}
                  · <strong>{entry.client}</strong> · {entry.operation} · {entry.access} · {entry.durationMs} ms{' '}
                  · <time dateTime={entry.timestamp}>{formatTime(entry.timestamp)}</time>
                  {entry.reason && <p className="muted mt-0.5">{entry.reason}</p>}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  )
}
