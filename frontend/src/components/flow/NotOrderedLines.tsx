import { MinusCircle } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import type { NoOrderDetails } from '../../lib/gateway'
import { notOrderedSentence } from '../approver/explain'

type Props = {
  proposal: Proposal
  noOrder?: NoOrderDetails | null
}

/** T106: requested lines this proposal leaves out. They are never ordered; the approver sees the same sentence. */
export function NotOrderedLines({ proposal, noOrder }: Props) {
  const sentence = notOrderedSentence(proposal)
  if (!sentence) return null
  const details = new Map((noOrder?.items ?? []).map((item) => [item.itemId, item]))
  return (
    <section
      className="outcome result-banner mt-2"
      aria-label="Not ordered"
      data-testid="not-ordered"
    >
      <p className="outcome-headline">
        <MinusCircle size={20} aria-hidden />
        <span data-testid="not-ordered-sentence">{sentence}</span>
      </p>
      {(proposal.notOrdered ?? []).map((item) => {
        const detail = details.get(item.itemId)
        return (
          <div className="mt-2 text-sm" key={item.itemId}>
            <p>
              <strong>Not ordered:</strong> {item.what}
              {detail ? ` (searched: ${detail.searched})` : ''}
            </p>
            {detail?.why && <p>{detail.why}</p>}
            {detail && detail.closestMatches.length > 0 && (
              <>
                <p className="mt-1">
                  <strong>Closest matches (not ordered)</strong>
                </p>
                <ul>
                  {detail.closestMatches.map((match) => (
                    <li key={match.productId}>
                    {match.name}
                    {match.lacks ? ` — ${match.lacks}` : ''}
                  </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )
      })}
    </section>
  )
}
