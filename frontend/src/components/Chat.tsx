import { useState } from 'react'
import { ArrowUp, Sparkles } from 'lucide-react'
import type { ChatResult, Proposal } from '../lib/types'
import { request, type Identity } from '../lib/api'
import { Button } from './ui/button'
import { ErrorNotice, Loading } from './Common'

export function Chat({
  identity,
  aiConfigured,
  onProposal,
}: {
  identity: Identity
  aiConfigured: boolean
  onProposal: (p: Proposal) => void
}) {
  const [message, setMessage] = useState('')
  const [answer, setAnswer] = useState<ChatResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function send(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await request<ChatResult>(identity, '/api/v1/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
      setAnswer(result)
      if (result.proposal) onProposal(result.proposal)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="chat-panel">
      <div className="section-heading">
        <div className="inline-icon">
          <Sparkles size={17} />
          <h3>Your purchasing copilot</h3>
        </div>
        <span className="mini-label">{aiConfigured ? 'OPENROUTER · TOOL USE' : 'GUIDED MODE'}</span>
      </div>
      <p>Describe what your kitchen needs. Every decision still goes through the gateway.</p>
      <form onSubmit={send} className="chat-input">
        <textarea
          aria-label="Message the purchasing assistant"
          placeholder="I need 100 litres of lactose-free milk by Monday, under €200…"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          maxLength={4000}
          required
          rows={2}
        />
        <Button size="icon" aria-label="Send message" disabled={busy || !message.trim()}>
          <ArrowUp size={20} />
        </Button>
      </form>
      {busy && <Loading label="Assistant is checking its tools…" />}
      <ErrorNotice error={error} />
      {answer && (
        <div className="chat-answer">
          <span className="mini-label">
            {answer.mode === 'ai' ? 'ASSISTANT' : 'GUIDED FALLBACK'}
          </span>
          <p>{answer.message}</p>
          {answer.tool_results.length > 0 && (
            <details>
              <summary>{answer.tool_results.length} verified tool results</summary>
              <pre>{JSON.stringify(answer.tool_results, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
      {!aiConfigured && (
        <span className="small muted">
          No AI key configured? The structured flow below works independently.
        </span>
      )}
    </section>
  )
}
