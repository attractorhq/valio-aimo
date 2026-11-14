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
    <span
      className="inline-flex flex-wrap items-center gap-1.5 text-xs"
      aria-label="Download decision log"
    >
      {(['json', 'csv'] as const).map((format) => (
        <a
          key={format}
          aria-label={`Download log (${format.toUpperCase()})`}
          className="log-download-btn"
          href={live ? exportUrl(proposalId, format) : localExportHref(trail, format)}
          download={exportFilename(proposalId, format)}
        >
          <Download size={12} aria-hidden /> {format.toUpperCase()}
        </a>
      ))}
    </span>
  )
}
