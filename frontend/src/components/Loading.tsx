/** Loading shapes reserve space without presenting invented values as data. */
export function Skeleton({ className = '' }: { className?: string }) {
  return <span className={`skeleton ${className}`} aria-hidden="true" />
}

export function LoadingSection({
  label,
  variant = 'list',
  className = '',
}: {
  label: string
  variant?: 'mandate' | 'list' | 'approval' | 'admin' | 'stats' | 'table' | 'status'
  className?: string
}) {
  return (
    <div
      className={`loading-section loading-${variant} ${className}`}
      role="status"
      aria-label={`Loading ${label}`}
      aria-busy="true"
      data-testid={`loading-${variant}`}
    >
      <span className="visually-hidden">Loading {label}…</span>
      <Skeleton className="skeleton-heading" />
      {variant === 'status' ? (
        <div className="skeleton-row">
          <Skeleton />
          <Skeleton className="skeleton-short" />
        </div>
      ) : variant === 'mandate' ? (
        <>
          <div className="skeleton-picker">
            <Skeleton />
            <Skeleton />
          </div>
          <div className="skeleton-limits">
            {[0, 1, 2].map((i) => (
              <div key={i}>
                <Skeleton />
                <Skeleton />
              </div>
            ))}
          </div>
          <Skeleton />
        </>
      ) : (
        <div className={variant === 'stats' ? 'skeleton-stats' : 'skeleton-rows'}>
          {Array.from({ length: variant === 'approval' ? 5 : 3 }, (_, i) => (
            <div className="skeleton-row" key={i}>
              <Skeleton />
              <Skeleton className="skeleton-short" />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
