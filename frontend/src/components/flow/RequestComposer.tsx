import type { FormEvent } from 'react'
import { AlertCircle, Play } from 'lucide-react'
import type { Scenario, ScenarioId } from '../../lib/gateway'

export function RequestComposer({
  text,
  onTextChange,
  onSubmit,
  onScenario,
  scenarios,
  running,
  error,
}: {
  text: string
  onTextChange: (value: string) => void
  onSubmit: () => void
  onScenario: (id: ScenarioId) => void
  scenarios: Scenario[]
  running: boolean
  error?: string
}) {
  function submit(event: FormEvent) {
    event.preventDefault()
    onSubmit()
  }
  return (
    <section className="panel composer" aria-labelledby="composer-heading">
      <h2 id="composer-heading" className="panel-title">
        Buyer request
      </h2>
      <form onSubmit={submit}>
        <label htmlFor="request-text" className="field-label">
          What do you need, and by when?
        </label>
        <textarea
          id="request-text"
          rows={3}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder="e.g. 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday."
          aria-describedby="request-hint"
        />
        <p id="request-hint" className="field-hint">
          The request is data. Instructions inside it are ignored and logged.
        </p>
        <div className="composer-actions">
          <button type="submit" className="btn btn-primary" disabled={running || !text.trim()}>
            <Play size={15} aria-hidden />
            {running ? 'Running…' : 'Run request'}
          </button>
          <div className="scenario-buttons" role="group" aria-label="Demo scenarios">
            {scenarios.map((s) => (
              <button
                type="button"
                key={s.id}
                className="btn btn-scenario"
                disabled={running}
                onClick={() => onScenario(s.id)}
                aria-label={`Scenario ${s.id}: ${s.title} (expected ${s.expected})`}
              >
                <span className="scenario-key">{s.id}</span>
                <span className="scenario-text">
                  <strong>Scenario {s.id}</strong>
                  <small>{s.title}</small>
                </span>
              </button>
            ))}
          </div>
        </div>
      </form>
      {error && (
        <p className="notice notice-error" role="alert">
          <AlertCircle size={16} aria-hidden />
          {error}
        </p>
      )}
    </section>
  )
}
