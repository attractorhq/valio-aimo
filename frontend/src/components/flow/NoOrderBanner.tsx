import { Ban } from 'lucide-react'
import { formatDate } from '../../lib/format'
import type { NoOrderDetails } from '../../lib/gateway'

type Props = {
  summary: string
  detail?: string
  noOrder?: NoOrderDetails | null
  hasExclusions: boolean
  onShowExclusions?: () => void
  onRetry?: (date: string) => void
}

export function NoOrderBanner({
  summary,
  detail,
  noOrder,
  hasExclusions,
  onShowExclusions,
  onRetry,
}: Props) {
  const earliest = noOrder?.earliestDelivery

  return (
    <section
      className="outcome outcome-block result-banner"
      aria-label="Result"
      data-testid="result-banner"
    >
      <p className="outcome-headline">
        <Ban size={22} aria-hidden />
        <span>
          <strong>No order · </strong>
          {summary}
        </span>
      </p>
      {!noOrder && detail && <p className="mt-1 text-sm">{detail}</p>}
      {noOrder?.items.map((item) => (
        <div className="mt-2 text-sm" key={item.itemId}>
          <p>
            <strong>Searched:</strong> {item.searched}
          </p>
          <p>{item.why}</p>
          {item.closestMatches.length > 0 && (
            <>
              <p className="mt-1">
                <strong>Closest matches (not ordered)</strong>
              </p>
              <ul>
                {item.closestMatches.map((match) => (
                  <li key={match.productId}>
                    {match.name}
                    {match.lacks ? ` — ${match.lacks}` : ''}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ))}
      {earliest && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <strong>Earliest delivery: {formatDate(earliest)}</strong>
          <button
            type="button"
            className="btn btn-scenario text-xs font-semibold"
            disabled={!onRetry}
            onClick={() => onRetry?.(earliest)}
          >
            Use {formatDate(earliest)}
          </button>
        </div>
      )}
      {hasExclusions && (
        <p className="result-links">
          <a href="#step-2" onClick={onShowExclusions}>
            Excluded candidates
          </a>
        </p>
      )}
    </section>
  )
}
