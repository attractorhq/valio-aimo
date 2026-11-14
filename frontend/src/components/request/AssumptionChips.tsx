import { useState, type FormEvent } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import type { Preference, RequestItem, StructuredRequest } from '../../lib/contracts'

export type RankedCandidateSummary = {
  productId: string
  name: string
}

export type ItemPlanSummary = {
  item: RequestItem
  shortlistIds: string[]
  rankedCandidates?: RankedCandidateSummary[]
}

const FAT_ASSUMPTION_RE = /^(?:any\s+fat(?:\s*(?:%|percent(?:age)?))?|\d+(?:[.,]\d+)?\s*%(?:\s*fat)?)$/i
const FAT_INPUT_RE = /^(\d+(?:[.,]\d+)?)\s*%?(?:\s*fat)?$/i
const SENTENCE_ASSUMPTION_RE = /\s*\([^()]+\)(?=\s*(?:[.;!\n]|$))/

export function formatPreferenceValue(pref: Preference): string {
  if (pref.kind === 'FAT_PERCENT') {
    const clean = pref.value.replace(/%/g, '').trim()
    return `${clean} %`
  }
  return pref.value
}

export function normaliseAssumptionLabel(label: string): string {
  if (/^any\s+fat\s+percentage$/i.test(label.trim())) {
    return 'Any fat %'
  }
  return label
}

/**
 * Build the list of assumption chips to display for a StructuredRequest (§7.2).
 * When a fat percentage preference is active (e.g. after changing "Any fat %" to "1.5 %"),
 * keep that fat percentage chip visible and editable in the assumptions list.
 */
export function buildAssumptionChips(request: StructuredRequest): string[] {
  const raw = (request.assumptions ?? []).map(normaliseAssumptionLabel)
  const fatPref = request.items
    .flatMap((item) => item.preferences ?? [])
    .find((p) => p.kind === 'FAT_PERCENT')

  if (!fatPref) {
    return raw
  }

  const fatChip = formatPreferenceValue(fatPref)
  if (raw.some((chip) => FAT_ASSUMPTION_RE.test(chip.trim()))) {
    return raw.map((chip) => (FAT_ASSUMPTION_RE.test(chip.trim()) ? fatChip : chip))
  }

  // Insert the active fat % chip right before "UHT or fresh" (where "Any fat %" normally appears)
  const uhtIdx = raw.findIndex((chip) => /uht\s+or\s+fresh/i.test(chip))
  if (uhtIdx >= 0) {
    return [...raw.slice(0, uhtIdx), fatChip, ...raw.slice(uhtIdx)]
  }
  return [...raw, fatChip]
}

function toRequestQualifier(oldChip: string, newChip: string): string | null {
  const trimmed = newChip.trim()
  if (!trimmed) return null
  if (/^any\s+fat/i.test(trimmed)) {
    return ''
  }
  const fatMatch = trimmed.match(FAT_INPUT_RE)
  if (fatMatch || FAT_ASSUMPTION_RE.test(oldChip.trim())) {
    if (fatMatch) {
      const num = fatMatch[1].replace(',', '.')
      if (num === '1.5') return '1.5 % semi skimmed'
      if (num === '0') return '0 % skimmed'
      if (num === '3.5') return '3.5 % whole'
      return `${num} %`
    }
  }
  return trimmed
}

/**
 * Produce the updated buyer request text when an assumption chip is edited (§7.2).
 * Inserts or updates the qualifier before trailing sentence punctuation so the deterministic parser
 * associates it with the item in that sentence.
 */
export function applyAssumptionEdit(
  requestText: string,
  oldChip: string,
  newChip: string,
): string {
  const qualifier = toRequestQualifier(oldChip, newChip)
  if (qualifier === null) return requestText

  const base = requestText.trim().replace(SENTENCE_ASSUMPTION_RE, '')
  if (qualifier === '') {
    return base
  }

  const firstPunct = base.match(/[.;!\n]/)
  if (firstPunct && firstPunct.index !== undefined) {
    const stem = base.slice(0, firstPunct.index).trimEnd()
    const rest = base.slice(firstPunct.index)
    return `${stem} (${qualifier})${rest}`
  }
  return `${base} (${qualifier})`
}

function candidateMatchesFatPreference(name: string, normFat: string): boolean {
  const lower = name.toLowerCase()
  if (normFat === '1.5') {
    if (
      lower.includes('1.5%') ||
      lower.includes('1,5%') ||
      lower.includes('1.5 %') ||
      lower.includes('1,5 %') ||
      lower.includes('semi skimmed') ||
      lower.includes('semi-skimmed') ||
      lower.includes('low fat') ||
      lower.includes('kevytmaito') ||
      lower.includes('lättmjölk')
    ) {
      return true
    }
    if (
      /\b3\s*%/.test(lower) ||
      /\b1\s*%/.test(lower) ||
      /\b0\s*%/.test(lower) ||
      lower.includes('whole milk') ||
      lower.includes('täysmaito') ||
      lower.includes('fat free') ||
      lower.includes('fatfree') ||
      lower.includes('rasvaton') ||
      /\bskimmed\b/.test(lower)
    ) {
      return false
    }
    return true
  }
  if (normFat === '0') {
    return (
      /\b0\s*%/.test(lower) ||
      lower.includes('fat free') ||
      lower.includes('fatfree') ||
      lower.includes('rasvaton') ||
      (!lower.includes('semi') && /\bskimmed\b/.test(lower))
    )
  }
  if (normFat === '3.5') {
    return (
      lower.includes('3.5%') ||
      lower.includes('3,5%') ||
      lower.includes('3.5 %') ||
      lower.includes('3,5 %') ||
      lower.includes('whole') ||
      lower.includes('täysmaito')
    )
  }
  const escaped = normFat.replace('.', '[.,]')
  const explicitRe = new RegExp(`\\b${escaped}\\s*%`, 'i')
  return explicitRe.test(lower)
}

/**
 * Filter an item's shortlist IDs according to any active fat % preference on the item.
 */
export function filterShortlistForItem(
  item: RequestItem,
  shortlistIds: string[],
  rankedCandidates?: RankedCandidateSummary[],
): string[] {
  const fatPref = (item.preferences ?? []).find((p) => p.kind === 'FAT_PERCENT')
  if (!fatPref || !rankedCandidates?.length) {
    return shortlistIds
  }
  const normFat = fatPref.value.replace(',', '.').replace(/%/g, '').trim()
  const nameById = new Map(rankedCandidates.map((c) => [c.productId, c.name]))
  const filtered = shortlistIds.filter((id) => {
    const name = nameById.get(id)
    if (!name) return true
    return candidateMatchesFatPreference(name, normFat)
  })
  return filtered.length > 0 ? filtered : shortlistIds
}

export function AssumptionChips({
  request,
  onEditAssumption,
  disabled = false,
}: {
  request: StructuredRequest
  onEditAssumption?: (nextRequestText: string, oldChip: string, newChip: string) => void
  disabled?: boolean
}) {
  const chips = buildAssumptionChips(request)
  const [editingChip, setEditingChip] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  if (!chips.length) {
    return <span className="muted">None</span>
  }

  function startEdit(chip: string) {
    if (!onEditAssumption || disabled) return
    setEditingChip(chip)
    setDraft(chip)
  }

  function cancelEdit() {
    setEditingChip(null)
    setDraft('')
  }

  function submitEdit(event: FormEvent) {
    event.preventDefault()
    if (!editingChip || !onEditAssumption) return
    const nextValue = draft.trim()
    const currentChip = editingChip
    setEditingChip(null)
    setDraft('')
    if (!nextValue || nextValue.toLowerCase() === currentChip.toLowerCase()) {
      return
    }
    const nextRequestText = applyAssumptionEdit(request.requestText, currentChip, nextValue)
    onEditAssumption(nextRequestText, currentChip, nextValue)
  }

  return (
    <ul className="chip-list" aria-label="Assumptions">
      {chips.map((chip) => {
        const isEditing = editingChip === chip
        if (isEditing) {
          return (
            <li key={chip} className="inline-flex items-center">
              <form onSubmit={submitEdit} className="inline-flex flex-wrap items-center gap-1">
                <input
                  type="text"
                  className="rounded-md border border-[var(--green)] bg-[var(--surface)] px-2 py-0.5 text-xs font-semibold text-[var(--ink)]"
                  style={{ width: `${Math.max(draft.length + 3, 9)}ch` }}
                  aria-label={`Edit assumption ${chip}`}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.preventDefault()
                      cancelEdit()
                    }
                  }}
                  autoFocus
                />
                <button
                  type="submit"
                  className="chip border-[var(--green)] bg-[var(--green)] text-white cursor-pointer"
                  aria-label={`Apply assumption ${chip}`}
                >
                  <Check size={11} aria-hidden />
                  <span>Apply</span>
                </button>
                <button
                  type="button"
                  className="chip cursor-pointer"
                  onClick={cancelEdit}
                  aria-label="Cancel editing assumption"
                >
                  <X size={11} aria-hidden />
                </button>
              </form>
            </li>
          )
        }

        if (!onEditAssumption) {
          return (
            <li key={chip} className="chip">
              <span>{chip}</span>
            </li>
          )
        }

        return (
          <li key={chip}>
            <button
              type="button"
              className="chip cursor-pointer hover:border-[var(--green)] hover:bg-[var(--green-soft)]"
              disabled={disabled}
              onClick={() => startEdit(chip)}
              aria-label={`Edit assumption: ${chip}`}
              title="Click to edit assumption and re-run the flow"
            >
              <span>{chip}</span>
              <Pencil size={11} aria-hidden />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
