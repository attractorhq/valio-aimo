import { AlertCircle, LoaderCircle } from 'lucide-react'
import type { Proposal } from '../lib/types'

export function Loading({ label = 'Checking with the gateway…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={18} className="spin" />
      {label}
    </div>
  )
}
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="error-notice" role="alert">
      <AlertCircle size={18} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  )
}
const labels: Record<Proposal['status'], string> = {
  AUTO_APPROVED: 'Within authority',
  NEEDS_APPROVAL: 'Needs approval',
  APPROVED: 'Manager approved',
  REJECTED: 'Not authorized',
  COMMITTED: 'Simulated order',
}
export function StatusBadge({ status }: { status: Proposal['status'] }) {
  return (
    <span className={`badge status-${status.toLowerCase()}`}>
      <i />
      {labels[status]}
    </span>
  )
}
export function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-glyph">↗</div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}
