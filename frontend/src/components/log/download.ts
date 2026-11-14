import type { AuditEntry, AuditTrail } from '../../lib/contracts'

// Decision-log downloads (T47). On the live gateway the server export is authoritative
// (`GET /v0.1/audit/export`); in mock mode the same JSON and CSV are built here from the shown log.

const CSV_COLUMNS = [
  'timestamp',
  'entryId',
  'customerId',
  'proposalId',
  'offerId',
  'offerVersion',
  'offerHash',
  'mandateVersion',
  'actor',
  'event',
  'fromState',
  'toState',
  'rule',
  'reasons',
  'summary',
  'comment',
  'facts',
] as const

const FORMULA_START = ['=', '+', '-', '@', '\t', '\r']

/** Plain text for a CSV cell; a leading formula character is escaped with an apostrophe (CWE-1236). */
function cell(value: unknown): string {
  let text = value === null || value === undefined ? '' : String(value)
  if (FORMULA_START.some((c) => text.startsWith(c))) text = `'${text}`
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function factsCell(entry: AuditEntry): string {
  return (entry.facts ?? [])
    .map((f) => `${f.fact}: ${f.display} [${f.level}${f.conflict ? ` · ${f.conflict.code}` : ''}]`)
    .join(' | ')
}

/** The same columns as the server export: one row per entry, oldest first. */
export function auditCsv(trail: AuditTrail): string {
  const rows = trail.entries.map((e) =>
    [
      e.timestamp,
      e.entryId,
      e.customerId,
      e.proposalId,
      e.offerId,
      e.offerVersion,
      e.offerHash,
      e.mandateVersion,
      e.actor,
      e.event,
      e.fromState,
      e.toState,
      e.rule,
      (e.reasons ?? []).join(';'),
      e.summary,
      e.comment,
      factsCell(e),
    ]
      .map(cell)
      .join(','),
  )
  return [CSV_COLUMNS.join(','), ...rows].join('\r\n') + '\r\n'
}

export function exportFilename(proposalId: string, format: 'json' | 'csv'): string {
  return `decision-log-proposal-${proposalId.replace(/[^A-Za-z0-9._-]+/g, '_')}.${format}`
}

/** The server export URL for one proposal's decision log. */
export function exportUrl(proposalId: string, format: 'json' | 'csv'): string {
  return `/v0.1/audit/export?proposalId=${encodeURIComponent(proposalId)}&format=${format}`
}

/** A self-contained link to the shown log (mock mode). UTF-8 with BOM for CSV, like the server. */
export function localExportHref(trail: AuditTrail, format: 'json' | 'csv'): string {
  return format === 'csv'
    ? `data:text/csv;charset=utf-8,${encodeURIComponent('﻿' + auditCsv(trail))}`
    : `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(trail, null, 2))}`
}
