import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import type { Proposal } from '../lib/contracts'
import { gateway, SCENARIOS, type FlowResult, type ScenarioId } from '../lib/gateway'
import { formatMoney } from '../lib/format'
import { MandatePanel } from '../components/flow/MandatePanel'
import { RequestComposer } from '../components/flow/RequestComposer'
import { ModeBadge } from '../components/flow/ModeBadge'
import { Step, type StepStatus } from '../components/flow/Step'
import { RequestCard, approvalLabel } from '../components/flow/StepContent'
import { DecisionFooter } from '../components/order/DecisionFooter'
import { ExcludedList } from '../components/order/ExcludedList'
import { OrderProposalView } from '../components/order/OrderProposalView'

function approvalStatus(proposal: Proposal): StepStatus {
  if (proposal.decision === 'BLOCK') return 'blocked'
  if (proposal.state === 'CONFIRMED_SIMULATED') return 'done'
  return 'waiting'
}

function itemCount(proposal: Proposal) {
  const n = proposal.request.items.length
  return `${n} item${n === 1 ? '' : 's'}`
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
  const proposal = run.data?.proposal
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
          <h2 className="panel-title">Flow</h2>
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
            statusWord={
              proposal ? { AUTO: 'Auto', ASK: 'Ask', BLOCK: 'Block' }[proposal.decision] : undefined
            }
            current={Boolean(proposal)}
            summary={proposal ? approvalLabel(proposal) : 'Agent decides, or a human signs off'}
          >
            {proposal && <DecisionFooter proposal={proposal} mandate={mandate} />}
          </Step>
        </ol>
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
