import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import type { Proposal } from '../lib/contracts'
import { gateway, SCENARIOS, type FlowResult, type ScenarioId } from '../lib/gateway'
import { formatMoney, reasonChip } from '../lib/format'
import { MandatePanel } from '../components/flow/MandatePanel'
import { MandateControls } from '../components/mandate/MandateControls'
import { RequestComposer } from '../components/flow/RequestComposer'
import { ModeBadge } from '../components/flow/ModeBadge'
import { StatusPill, Step, type StepStatus } from '../components/flow/Step'
import { RequestCard, approvalLabel } from '../components/flow/StepContent'
import { DecisionFooter } from '../components/order/DecisionFooter'
import { ExcludedList } from '../components/order/ExcludedList'
import { OrderProposalView } from '../components/order/OrderProposalView'
import { logPath } from '../components/log/logData'

function approvalStatus(proposal: Proposal): StepStatus {
  if (proposal.decision === 'BLOCK') return 'blocked'
  if (proposal.state === 'CONFIRMED_SIMULATED') return 'done'
  if (['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)) return 'blocked'
  return 'waiting'
}

const STATE_WORD: Partial<Record<Proposal['state'], string>> = {
  REJECTED: 'Rejected',
  EXPIRED: 'Expired',
  SUPERSEDED: 'Superseded',
}

function approvalWord(proposal: Proposal): string {
  if (proposal.decision === 'ASK' && proposal.state === 'CONFIRMED_SIMULATED') return 'Approved'
  return (
    STATE_WORD[proposal.state] ?? { AUTO: 'Auto', ASK: 'Ask', BLOCK: 'Block' }[proposal.decision]
  )
}

function itemCount(proposal: Proposal) {
  const n = proposal.request.items.length
  return `${n} item${n === 1 ? '' : 's'}`
}

/** A superseded proposal points to the version decided from scratch after it; show the newest one. */
async function latestVersion(proposalId: string): Promise<Proposal> {
  let proposal = await gateway.getProposal(proposalId)
  for (
    let hops = 0;
    proposal.state === 'SUPERSEDED' && proposal.supersededBy && hops < 10;
    hops++
  ) {
    proposal = await gateway.getProposal(proposal.supersededBy)
  }
  return proposal
}

/** The one main view: Request → Lookup → Order → Approval, around a structured flow (not a chat). */
export function Main() {
  const mandates = useQuery({ queryKey: ['mandates'], queryFn: () => gateway.listMandates() })
  const [customerId, setCustomerId] = useState<string>()
  const [text, setText] = useState('')
  const run = useMutation<FlowResult, Error, { customerId: string; requestText: string }>({
    mutationFn: (input) => gateway.run(input),
  })

  useEffect(() => {
    if (!customerId && mandates.data?.length) setCustomerId(mandates.data[0].customerId)
  }, [customerId, mandates.data])

  const mandate = mandates.data?.find((m) => m.customerId === customerId)
  // Follow the proposal's state on the agent channel: the approver decides in a separate window.
  const latest = useQuery({
    queryKey: ['proposal', run.data?.proposal.proposalId],
    queryFn: () => latestVersion(run.data!.proposal.proposalId),
    enabled: Boolean(run.data),
    refetchInterval: (query) =>
      (query.state.data ?? run.data?.proposal)?.state === 'PENDING_APPROVAL' ? 1500 : false,
  })
  const proposal = latest.data ?? run.data?.proposal
  const running = run.isPending

  function selectMandate(id: string) {
    setCustomerId(id)
    run.reset()
  }
  function submit() {
    if (customerId && text.trim()) run.mutate({ customerId, requestText: text })
  }
  function scenario(id: ScenarioId) {
    const s = SCENARIOS.find((x) => x.id === id)!
    setCustomerId(s.customerId)
    setText(s.requestText)
    run.mutate({ customerId: s.customerId, requestText: s.requestText })
  }

  const done: StepStatus = proposal ? 'done' : running ? 'running' : 'idle'
  return (
    <div className="main-grid">
      <aside className="main-side">
        {mandates.error ? (
          <p className="notice notice-error" role="alert">
            Could not load mandates.
          </p>
        ) : (
          <MandatePanel
            mandates={mandates.data ?? []}
            selected={mandate}
            onSelect={selectMandate}
            disabled={running}
          />
        )}
        {mandate && (
          <MandateControls
            mandate={mandate}
            live={gateway.source === 'GATEWAY'}
            onChanged={() => {
              mandates.refetch()
              latest.refetch()
            }}
          />
        )}
      </aside>
      <div className="main-flow">
        <RequestComposer
          text={text}
          onTextChange={setText}
          onSubmit={submit}
          onScenario={scenario}
          scenarios={SCENARIOS}
          running={running}
          error={run.error?.message}
        />
        <div className="flow-header">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="panel-title">Flow</h2>
            {proposal && (
              <div
                className="flex flex-wrap items-center gap-2 text-xs"
                aria-label="Decision summary"
              >
                <StatusPill status={approvalStatus(proposal)} word={approvalWord(proposal)} />
                <strong>{approvalLabel(proposal)}</strong>
                {proposal.reasons.map((code) => (
                  <span key={code} className="chip">
                    {reasonChip(code)}
                  </span>
                ))}
                <a href="#step-4" className="font-semibold text-[var(--green)] underline">
                  Jump to step 4
                </a>
              </div>
            )}
          </div>
          <ModeBadge source={gateway.source} proposal={proposal} />
        </div>
        <ol className="steps" aria-label="Purchasing flow">
          <Step
            number={1}
            title="Request"
            status={done}
            summary={proposal ? `${itemCount(proposal)} · structured` : 'Structured request'}
          >
            {proposal && <RequestCard proposal={proposal} />}
          </Step>
          <Step
            number={2}
            title="Lookup"
            status={done}
            summary="Static catalogue first, then live price + availability for the shortlist only"
          >
            {proposal && <ExcludedList proposal={proposal} />}
          </Step>
          <Step
            number={3}
            title="Order"
            status={done}
            summary={
              proposal
                ? `Proposal at fixed price · offer v${proposal.offerVersion} · ${formatMoney(proposal.totals.exclVat)} excl. VAT`
                : 'Proposal at fixed price'
            }
          >
            {proposal && <OrderProposalView proposal={proposal} />}
          </Step>
          <Step
            number={4}
            title="Approval"
            status={proposal ? approvalStatus(proposal) : running ? 'running' : 'idle'}
            statusWord={proposal ? approvalWord(proposal) : undefined}
            current={Boolean(proposal)}
            summary={proposal ? approvalLabel(proposal) : 'Agent decides, or a human signs off'}
          >
            {proposal && <DecisionFooter proposal={proposal} mandate={mandate} />}
          </Step>
        </ol>
        {proposal && (
          <p>
            <a
              href={logPath(proposal.proposalId)}
              className="font-semibold text-[var(--green)] underline"
            >
              Open the decision log for this proposal
            </a>
          </p>
        )}
        <p className="visually-hidden" aria-live="polite">
          {proposal
            ? `Decision ${proposal.decision}. ${approvalLabel(proposal)}.`
            : running
              ? 'Running request.'
              : ''}
        </p>
      </div>
    </div>
  )
}
