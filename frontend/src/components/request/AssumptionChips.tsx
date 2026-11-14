import { useState, type FormEvent } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import type { Preference, RequestItem, StructuredRequest } from '../../lib/contracts'

export type ItemPlanSummary = {
  item: RequestItem
  shortlistIds: string[]
}

const FAT_ASSUMPTION_RE =
  /^(?:any\s+fat(?:\s*(?:%|percent(?:age)?))?|\d+(?:[.,]\d+)?\s*%(?:\s*fat)?)$/i
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
export function applyAssumptionEdit(requestText: string, oldChip: string, newChip: string): string {
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
  const fatChipIndex = chips.findIndex((chip) => FAT_ASSUMPTION_RE.test(chip.trim()))
  if (fatChipIndex > 1) chips.unshift(...chips.splice(fatChipIndex, 1))
  const [editingChip, setEditingChip] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [showMore, setShowMore] = useState(false)

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
      {(showMore ? chips : chips.slice(0, 2)).map((chip) => {
        const isEditing = editingChip === chip
        if (isEditing) {
          return (
            <li key={chip} className="inline-flex items-center">
              <form onSubmit={submitEdit} className="inline-flex flex-wrap items-center gap-1">
                <input
                  type="text"
                  className="rounded-[12px] border border-[var(--action)] bg-[var(--surface)] px-2.5 py-0.5 text-xs font-semibold text-[var(--ink)]"
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
                  className="chip cursor-pointer rounded-full border-[var(--action)] bg-[var(--action)] px-3 text-white hover:bg-[var(--action-hover)]"
                  aria-label={`Apply assumption ${chip}`}
                >
                  <Check size={11} aria-hidden />
                  <span>Apply</span>
                </button>
                <button
                  type="button"
                  className="chip cursor-pointer rounded-full border-[var(--action)] bg-[var(--surface)] text-[var(--action)] hover:bg-[var(--tint)]"
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
      {chips.length > 2 && (
        <li>
          <button
            type="button"
            className="disclosure-btn"
            aria-expanded={showMore}
            onClick={() => setShowMore(!showMore)}
          >
            {showMore ? 'Show fewer assumptions' : `+${chips.length - 2} more`}
          </button>
        </li>
      )}
    </ul>
  )
}
