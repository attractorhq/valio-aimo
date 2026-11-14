import { useId, useState, type ReactNode } from 'react'

/** A short label with an explanation available by hover, keyboard focus or tap. */
export function EvidenceChip({
  children,
  detail,
  label,
  className,
}: {
  children: ReactNode
  detail: ReactNode
  label?: string
  className?: string
}) {
  const id = useId()
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const open = !dismissed && (hovered || focused || pinned)

  return (
    <span
      className="evidence-chip"
      onPointerEnter={(event) => {
        if (event.pointerType !== 'touch') {
          setHovered(true)
          setDismissed(false)
        }
      }}
      onPointerLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className={`evidence-chip-button ${className ?? ''}`}
        aria-label={label}
        aria-describedby={id}
        onFocus={() => {
          setFocused(true)
          setDismissed(false)
        }}
        onBlur={() => {
          setFocused(false)
          setPinned(false)
        }}
        onClick={() => {
          setPinned(!pinned)
          setDismissed(pinned)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setPinned(false)
            setDismissed(true)
          }
        }}
      >
        {children}
      </button>
      <span className="evidence-tooltip" id={id} role="tooltip" hidden={!open}>
        {detail}
      </span>
    </span>
  )
}
