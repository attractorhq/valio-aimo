import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, KeyRound, Smartphone, UserRoundCheck } from 'lucide-react'
import { useParams, useSearchParams } from 'react-router-dom'
import { ApprovalCard } from '../components/approver/ApprovalCard'
import { approverChannel, gateway } from '../lib/gateway'

/**
 * `/approve/:token` phone sign-off page (P3, P10 S3).
 * Opened via the QR code in the Approver panel; carries the exact same proposal content and single-use demo link notice.
 */
export function ApproveMobile() {
  const { token = '' } = useParams<{ token: string }>()
  const [searchParams] = useSearchParams()
  const decodedToken = decodeURIComponent(token)
  const proposalIdHint = searchParams.get('proposalId') ?? undefined

  const mandates = useQuery({
    queryKey: ['mandates'],
    queryFn: () => gateway.listMandates(),
  })

  const view = useQuery({
    queryKey: ['approver-token-view', decodedToken, proposalIdHint],
    queryFn: () => approverChannel.viewByToken(decodedToken, proposalIdHint),
    enabled: Boolean(decodedToken),
    retry: false,
  })

  const proposal = view.data?.proposal
  const mandate = mandates.data?.find((m) => m.customerId === proposal?.customerId)
  const role = view.data?.approver.role ?? mandate?.approver.role ?? 'Head Chef (demo)'

  return (
    <div className="approver-page mx-auto max-w-xl">
      <header className="approver-header">
        <UserRoundCheck size={24} aria-hidden />
        <div>
          <h1>Approver — {role}</h1>
          <p className="flex flex-wrap items-center gap-1.5 text-xs">
            <Smartphone size={14} aria-hidden />
            <span>Phone sign-off · Human sign-off channel · not the agent</span>
          </p>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-[var(--line)] bg-[var(--surface)] px-2.5 py-1 text-xs font-semibold text-[var(--ink)]">
          <KeyRound size={12} aria-hidden />
          <span>{view.data?.permissionNotice ?? 'Demo approval link — single use'}</span>
        </span>
      </header>

      {view.isPending && (
        <p className="panel muted" role="status">
          Loading proposal for approval…
        </p>
      )}

      {view.error && (
        <div className="panel notice notice-error" role="alert">
          <AlertTriangle size={18} aria-hidden />
          <div>
            <strong>Cannot open approval link</strong>
            <p className="mt-1 text-sm">
              {view.error instanceof Error
                ? view.error.message
                : 'This single-use demo approval link is invalid or has expired.'}
            </p>
          </div>
        </div>
      )}

      {view.data && (
        <ApprovalCard
          key={`${view.data.proposal.proposalId}-${view.data.proposal.state}`}
          item={{ proposal: view.data.proposal, loggedAt: view.data.loggedAt }}
          mandate={mandate}
          showQr={false}
          initialToken={decodedToken}
          initialApprovalStatement={view.data.approvalStatement}
        />
      )}
    </div>
  )
}
