import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertCircle, HelpCircle, Pencil } from 'lucide-react'
import type { Proposal } from '../lib/contracts'
import {
  gateway,
  SCENARIOS,
  scenarioCustomerId,
  subscribeGatewaySource,
  type AgentStepEvent,
  type DataSource,
  type FlowResult,
  type ScenarioId,
} from '../lib/gateway'
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

function itemCountFromItems(n: number) {
  return `${n} item${n === 1 ? '' : 's'}`
}

const IN_FLIGHT = new Set(['PENDING_APPROVAL', 'APPROVED', 'AUTO_APPROVED', 'RECHECKED'])

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
  const health = useQuery({
    queryKey: ['gateway-health'],
    queryFn: () => gateway.checkHealth(),
    retry: false,
  })
  const [customerId, setCustomerId] = useState<string>()
  const [text, setText] = useState('')
  const [streamedSteps, setStreamedSteps] = useState<AgentStepEvent[]>([])
  const [sourceState, setSourceState] = useState<DataSource>(() => gateway.source)

  useEffect(() => {
    return subscribeGatewaySource((next) => setSourceState(next))
  }, [])

  const run = useMutation<FlowResult, Error, { customerId: string; requestText: string }>({
    mutationFn: (input) => {
      setStreamedSteps([])
      return gateway.run({
        ...input,
        onStep: (_step, stepsSoFar) => {
          setStreamedSteps(stepsSoFar)
        },
      })
    },
  })

  useEffect(() => {
    if (!customerId && mandates.data?.length) setCustomerId(mandates.data[0].customerId)
  }, [customerId, mandates.data])

  const mandate = mandates.data?.find((m) => m.customerId === customerId)
  // Follow the proposal's state on the agent channel: the approver decides in a separate window.
  const latest = useQuery({
    queryKey: ['proposal', run.data?.proposal?.proposalId],
    queryFn: () => latestVersion(run.data!.proposal!.proposalId),
    enabled: Boolean(run.data?.proposal?.proposalId),
    // Keep polling through the short re-check after a sign-off (APPROVED → RECHECKED → CONFIRMED), so the
    // buyer view never stops on a transient state.
    refetchInterval: (query) =>
      IN_FLIGHT.has((query.state.data ?? run.data?.proposal)?.state ?? '') ? 1500 : false,
  })

  // If the proposal was superseded at re-check, also fetch the superseding v2 proposal for display context
  const rawProposal = latest.data ?? run.data?.proposal ?? undefined
  const supersededNext = useQuery({
    queryKey: ['proposal', rawProposal?.supersededBy],
    queryFn: () => gateway.getProposal(rawProposal!.supersededBy!),
    enabled: Boolean(rawProposal?.state === 'SUPERSEDED' && rawProposal?.supersededBy),
  })
  const proposal = rawProposal
  const running = run.isPending

  const activeSteps = run.data?.steps ?? streamedSteps
  const step1 = activeSteps.find((s) => s.step === 1 && s.name === 'REQUEST')
  const step2 = activeSteps.find((s) => s.step === 2 && s.name === 'LOOKUP')
  const step3 = activeSteps.find((s) => s.step === 3 && s.name === 'ORDER')
  const step4 = activeSteps.find((s) => s.step === 4 && s.name === 'APPROVAL')
  const errorStep = activeSteps.find((s) => s.name === 'ERROR')

  const clarificationQuestion =
    run.data?.clarificationQuestion ?? step1?.clarificationQuestion ?? undefined
  const streamError = run.data?.error ?? errorStep?.error ?? undefined

  const structuredRequest =
    proposal?.request ??
    run.data?.request ??
    step3?.request ??
    step2?.request ??
    step1?.request ??
    undefined
  const lookupExcluded = proposal?.excluded ?? run.data?.excluded ?? step2?.excluded ?? undefined
  const offerSnapshot = run.data?.offer ?? step3?.offer ?? undefined

  function selectMandate(id: string) {
    setCustomerId(id)
    setStreamedSteps([])
    run.reset()
  }
  function submit() {
    if (customerId && text.trim()) {
      setStreamedSteps([])
      run.mutate({ customerId, requestText: text })
    }
  }
  function scenario(id: ScenarioId) {
    const s = SCENARIOS.find((x) => x.id === id)!
    const targetCustomerId = scenarioCustomerId(id, mandates.data ?? [])
    setCustomerId(targetCustomerId)
    setText(s.requestText)
    setStreamedSteps([])
    run.mutate({ customerId: targetCustomerId, requestText: s.requestText })
  }
  function focusComposerToEdit() {
    const el = document.getElementById('request-text') as HTMLTextAreaElement | null
    if (el) {
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
    }
  }

  const step1Status: StepStatus = step1
    ? step1.status
    : proposal
      ? 'done'
      : running
        ? 'running'
        : 'idle'

  const step2Status: StepStatus =
    errorStep && errorStep.step <= 2
      ? 'blocked'
      : step2
        ? step2.status
        : proposal
          ? 'done'
          : running && step1?.status === 'done'
            ? 'running'
            : 'idle'

  const step3Status: StepStatus =
    errorStep && errorStep.step === 3
      ? 'blocked'
      : step3
        ? step3.status
        : proposal
          ? 'done'
          : running && step2?.status === 'done'
            ? 'running'
            : 'idle'

  const step4Status: StepStatus = proposal
    ? approvalStatus(proposal)
    : errorStep && errorStep.step === 4
      ? 'blocked'
      : step4
        ? step4.status
        : running && step3?.status === 'done'
          ? 'running'
          : 'idle'

  const currentStepNumber =
    proposal || step4 || (errorStep && errorStep.step === 4)
      ? 4
      : step3 || (errorStep && errorStep.step === 3)
        ? 3
        : step2 || (errorStep && errorStep.step === 2)
          ? 2
          : step1
            ? 1
            : undefined

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
            live={sourceState === 'GATEWAY'}
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
          error={run.error?.message ?? streamError?.message}
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
          <ModeBadge
            source={run.data?.source ?? sourceState}
            proposal={proposal}
            healthMode={run.data?.mode ?? health.data?.mode ?? gateway.healthMode}
          />
        </div>
        <ol className="steps" aria-label="Purchasing flow">
          <Step
            number={1}
            title="Request"
            status={step1Status}
            current={currentStepNumber === 1 && !proposal}
            summary={
              clarificationQuestion
                ? clarificationQuestion
                : structuredRequest
                  ? `${itemCountFromItems(structuredRequest.items.length)} · structured`
                  : (step1?.summary ?? 'Structured request')
            }
          >
            {clarificationQuestion && (
              <div
                className="outcome outcome-ask mb-2"
                role="region"
                aria-label="Clarification needed"
              >
                <p className="outcome-headline">
                  <HelpCircle size={20} aria-hidden />
                  <span>
                    <span className="outcome-word">CLARIFICATION NEEDED</span>
                    {clarificationQuestion}
                  </span>
                </p>
                {run.data?.explanation?.text && (
                  <p className="mt-1 text-sm">{run.data.explanation.text}</p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn btn-scenario text-xs font-semibold"
                    onClick={focusComposerToEdit}
                  >
                    <Pencil size={14} aria-hidden />
                    <span>Answer by editing the request above</span>
                  </button>
                  <span className="text-xs text-[var(--ink)] opacity-80">
                    Nothing was ordered. Add the missing detail and run again.
                  </span>
                </div>
              </div>
            )}
            {structuredRequest && <RequestCard proposal={proposal} request={structuredRequest} />}
          </Step>
          <Step
            number={2}
            title="Lookup"
            status={step2Status}
            current={currentStepNumber === 2 && !proposal}
            summary={
              errorStep && errorStep.step <= 2
                ? errorStep.summary
                : 'Static catalogue first, then live price + availability for the shortlist only'
            }
          >
            {errorStep && errorStep.step <= 2 && (
              <div className="notice notice-error mb-2" role="alert">
                <AlertCircle size={16} aria-hidden />
                <span>{errorStep.summary}</span>
              </div>
            )}
            {(proposal || step2 || lookupExcluded?.length) && (
              <ExcludedList
                proposal={proposal}
                request={structuredRequest}
                excluded={lookupExcluded}
              />
            )}
          </Step>
          <Step
            number={3}
            title="Order"
            status={step3Status}
            current={currentStepNumber === 3 && !proposal}
            summary={
              proposal
                ? `Proposal at fixed price · offer v${proposal.offerVersion} · ${formatMoney(proposal.totals.exclVat)} excl. VAT`
                : offerSnapshot
                  ? `Proposal at fixed price · offer v${offerSnapshot.offerVersion} · ${formatMoney(offerSnapshot.totals.exclVat)} excl. VAT`
                  : (step3?.summary ?? 'Proposal at fixed price')
            }
          >
            {(proposal || offerSnapshot) && (
              <OrderProposalView
                proposal={proposal}
                offer={offerSnapshot}
                request={structuredRequest}
                mandateVersion={mandate?.version}
              />
            )}
          </Step>
          <Step
            number={4}
            title="Approval"
            status={step4Status}
            statusWord={proposal ? approvalWord(proposal) : undefined}
            current={Boolean(proposal || step4 || (errorStep && errorStep.step === 4))}
            summary={
              proposal
                ? approvalLabel(proposal)
                : errorStep && errorStep.step === 4
                  ? errorStep.summary
                  : (step4?.summary ?? 'Agent decides, or a human signs off')
            }
          >
            {proposal && <DecisionFooter proposal={proposal} mandate={mandate} />}
            {!proposal && step4 && (
              <div className="outcome outcome-block" role="status">
                <p className="outcome-headline">
                  <AlertCircle size={20} aria-hidden />
                  <span>
                    <span className="outcome-word">NO ORDER</span>
                    {step4.summary}
                  </span>
                </p>
                {step4.explanation?.text && (
                  <p className="mt-1 text-sm">{step4.explanation.text}</p>
                )}
              </div>
            )}
            {!proposal && errorStep && errorStep.step === 4 && (
              <div className="notice notice-error" role="alert">
                <AlertCircle size={16} aria-hidden />
                <span>{errorStep.summary}</span>
              </div>
            )}
            {proposal?.state === 'SUPERSEDED' && supersededNext.data && (
              <div className="mt-2 rounded-lg border border-[var(--ask)] bg-[var(--ask-bg)] p-3 text-sm">
                <strong>New version decided from scratch:</strong> proposal{' '}
                <code>{supersededNext.data.proposalId}</code> (offer v
                {supersededNext.data.offerVersion},{' '}
                {formatMoney(supersededNext.data.totals.exclVat)} excl. VAT) →{' '}
                <strong>{supersededNext.data.decision}</strong> ({supersededNext.data.state}).
              </div>
            )}
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
            : clarificationQuestion
              ? `Clarification needed: ${clarificationQuestion}`
              : streamError
                ? `Error: ${streamError.message}`
                : running
                  ? 'Running request.'
                  : ''}
        </p>
      </div>
    </div>
  )
}
