import { Fragment } from 'react'

const PROPOSAL_ID_RE = /\b(PR-[A-Za-z0-9_-]+)\b/g

/** Render plain text with any `PR-…` proposal reference linked to its decision log (T102). */
export function ProposalRefText({ text }: { text: string }) {
  const parts = text.split(PROPOSAL_ID_RE)
  if (parts.length === 1) return <>{text}</>
  return (
    <>
      {parts.map((part, idx) =>
        idx % 2 === 1 ? (
          <a
            key={`${idx}-${part}`}
            href={`/log/${encodeURIComponent(part)}`}
            className="font-semibold text-[var(--valio-action-blue)] underline hover:text-[var(--valio-button-hover)]"
          >
            {part}
          </a>
        ) : (
          <Fragment key={idx}>{part}</Fragment>
        ),
      )}
    </>
  )
}
