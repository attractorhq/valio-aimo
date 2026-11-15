import { Download } from 'lucide-react'
import { useState } from 'react'
import type { AuditTrail } from '../../lib/contracts'
import type { DataSource } from '../../lib/gateway'
import { exportFilename, exportUrl, localExportHref } from './download'
import { agentHeaders } from '../../lib/demoAccess'

const peppolUrl = (proposalId: string) =>
  `/v0.1/proposals/${encodeURIComponent(proposalId)}/peppol-order`

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
  // T72: only a confirmed order has a Peppol order document.
  const confirmed = trail.entries.some((entry) => entry.toState === 'CONFIRMED_SIMULATED')
  const [error, setError] = useState('')

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
          onClick={async (event) => {
            if (!live) return
            event.preventDefault()
            setError('')
            try {
              const response = await fetch(exportUrl(proposalId, format), {
                headers: agentHeaders(trail.entries[0]?.customerId ?? ''),
              })
              if (!response.ok) throw new Error('Could not download this buyer’s log.')
              const url = URL.createObjectURL(await response.blob())
              const link = document.createElement('a')
              link.href = url
              link.download = exportFilename(proposalId, format)
              link.click()
              setTimeout(() => URL.revokeObjectURL(url), 1000)
            } catch {
              setError('Could not download this buyer’s log.')
            }
          }}
        >
          <Download size={12} aria-hidden /> {format.toUpperCase()}
        </a>
      ))}
      {live && confirmed && (
        <a
          aria-label="Download Peppol order (XML)"
          className="log-download-btn"
          href={peppolUrl(proposalId)}
          download={`peppol-order-${proposalId}.xml`}
          onClick={async (event) => {
            event.preventDefault()
            setError('')
            try {
              const response = await fetch(peppolUrl(proposalId), {
                headers: agentHeaders(trail.entries[0]?.customerId ?? ''),
              })
              if (!response.ok) throw new Error('Could not download the Peppol order.')
              const url = URL.createObjectURL(await response.blob())
              const link = document.createElement('a')
              link.href = url
              link.download = `peppol-order-${proposalId}.xml`
              link.click()
              setTimeout(() => URL.revokeObjectURL(url), 1000)
            } catch {
              setError('Could not download the Peppol order.')
            }
          }}
        >
          <Download size={12} aria-hidden /> Peppol order (XML)
        </a>
      )}
      {error && <span role="alert">{error}</span>}
    </span>
  )
}
