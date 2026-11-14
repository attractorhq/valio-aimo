import { Download } from 'lucide-react'
import type { AuditTrail } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { exportFilename, exportUrl, localExportHref } from './download'

/**
 * "Download log (JSON / CSV)" for one proposal (T47). Live: the gateway's export endpoint, so the file is
 * the server's own record. Mock: the shown log, built in the browser with the same columns.
 */
export function LogDownloads({
  proposalId,
  trail,
  source,
}: {
  proposalId: string
  trail: AuditTrail
  source: DataSource
}) {
  const live = source === 'GATEWAY'

  return (
    <p
      className="mt-2 flex flex-wrap items-center gap-2 text-sm"
      aria-label="Download decision log"
    >
      {(['json', 'csv'] as const).map((format) => (
        <a
          key={format}
          className="btn btn-scenario inline-flex items-center gap-1.5 font-semibold"
          href={live ? exportUrl(proposalId, format) : localExportHref(trail, format)}
          download={exportFilename(proposalId, format)}
        >
          <Download size={14} aria-hidden /> Download log ({format.toUpperCase()})
        </a>
      ))}
      <span className="muted small">
        {live ? 'The gateway’s own record of this proposal.' : 'The synthetic log shown below.'}
      </span>
    </p>
  )
}
