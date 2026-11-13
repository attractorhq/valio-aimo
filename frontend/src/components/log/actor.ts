// Who or what decided an audit entry: the agent, a deterministic gateway rule, or a named human.

export type ActorKind = 'AGENT' | 'GATEWAY' | 'HUMAN'

export type Actor = {
  kind: ActorKind
  /** Short word shown on the badge (icon + word, never colour alone). */
  word: string
  /** Who exactly, in plain language. */
  who: string
}

export function classifyActor(actor: string): Actor {
  const [head, ...rest] = actor.split(':')
  const detail = rest.join(':').trim()
  switch (head.trim().toLowerCase()) {
    case 'agent':
      return { kind: 'AGENT', word: 'Agent', who: detail || 'Purchasing agent' }
    case 'approver':
    case 'human':
    case 'buyer-admin':
      return { kind: 'HUMAN', word: 'Human', who: detail || 'Approver' }
    case 'gateway':
    case 'policy':
    case 'system':
      return {
        kind: 'GATEWAY',
        word: 'Gateway rule',
        who: detail === 'policy' ? 'Gateway policy (deterministic)' : 'Gateway',
      }
    default:
      // Unknown actors are shown as written, never guessed into a category.
      return { kind: 'GATEWAY', word: 'System', who: actor }
  }
}
