import { ArrowLeft, CheckCircle2, Download, FileJson, XCircle } from 'lucide-react'
import { REASON_REGISTRY, type ReasonDefinition, type TrustLevel } from '../lib/contracts'
import { TrustBadge } from '../components/trust/TrustBadge'

// The JSON Schemas published with the contract (spec/schemas/*.json), bundled at build time so the page
// works on the same origin without a separate file server. They are generated from backend/app/contracts.
const SCHEMA_FILES = import.meta.glob('../../../spec/schemas/*.json', {
  eager: true,
  import: 'default',
}) as Record<string, unknown>

const SCHEMAS = Object.entries(SCHEMA_FILES)
  .map(([path, schema]) => ({
    name: path
      .split('/')
      .pop()!
      .replace(/\.json$/, ''),
    schema,
  }))
  .sort((a, b) => a.name.localeCompare(b.name))

const LEVELS: { level: TrustLevel; meaning: string; examples: string }[] = [
  {
    level: 'VERIFIED',
    meaning: "From Valio Aimo's own live systems or Valio Aimo-defined rules.",
    examples:
      'Price API, availability API (warehouse stock), sales block, Valio substitution list.',
  },
  {
    level: 'DECLARED',
    meaning: 'Declared by the manufacturer or supplier in the product data (GS1 Synkka).',
    examples:
      'Allergens, free-from declarations, nutritional claims, labels, origin, pack content, ingredients.',
  },
  {
    level: 'ESTIMATE',
    meaning: 'Statistical or derived.',
    examples:
      'Historical delivery reliability, product-group CO₂ average, pack content from a price ratio or the name, agent-proposed substitutes.',
  },
  {
    level: 'UNKNOWN',
    meaning: 'Missing, undeclared or explicitly invisible. Always shown as "Unknown", never blank.',
    examples: 'No allergen declaration; supplier-warehouse stock whose quantity Valio cannot see.',
  },
]

const CONFLICTS = [
  [
    'K1',
    'Gluten cereals declared as contained while the product claims gluten-free.',
    'DATA_CONFLICT',
  ],
  ['K2', 'Lactose declared as contained while the product claims lactose-free.', 'DATA_CONFLICT'],
  ['K3', 'The order cut-off has passed while the delivery date is still offered.', 'CUTOFF_PASSED'],
  [
    'K4',
    'The availability sales unit differs from the product sales unit and cannot be converted exactly.',
    'UNIT_MISMATCH',
  ],
  ['K5', 'The two pack-content methods disagree by more than 5 %.', 'DATA_CONFLICT'],
] as const

const SOURCES = [
  ['valio-aimo.price-api', 'Live customer price and VAT rate'],
  ['valio-aimo.availability-api', 'Live stock, delivery date, cut-off, sales block'],
  ['valio-aimo.product-data', 'Supplier product data (GS1 Synkka)'],
  ['valio-aimo.substitution-rules', "Valio Aimo's own substitute list"],
  ['valio-aimo.delivery-reliability', 'Historical delivered-in-full rate'],
  ['valio-aimo.carbon-footprint', 'Product-group CO₂ average'],
  ['derived', 'Computed by the gateway (the method is always stated)'],
  ['agent', 'Proposed by the purchasing agent'],
] as const

const LABEL_FIELDS = [
  [
    'fact',
    'What the label is about, e.g. price.unit, stock.quantity, claim.FREE_FROM_GLUTEN, allergen.AM.',
  ],
  ['value', 'The value itself; money and quantities as decimal strings.'],
  ['display', 'The human-readable form shown in the UI.'],
  ['level', 'VERIFIED | DECLARED | ESTIMATE | UNKNOWN.'],
  ['source', 'system + field it came from, and retrievedAt for live facts.'],
  ['method', 'How a derived value was computed (required for derived facts).'],
  ['conflict', 'Optional K1–K5 flag with a one-line explanation.'],
] as const

const OPERATIONS: { channel: string; rows: [string, string, string][] }[] = [
  {
    channel: 'Agent channel: any purchasing agent may call these',
    rows: [
      [
        'searchProducts',
        'POST /v0.1/products/search',
        'Static lookup with hard requirements and an excluded list.',
      ],
      [
        'getOffer',
        'POST /v0.1/offers',
        'Live price and availability for the shortlist; an offer at fixed prices.',
      ],
      [
        'createProposal',
        'POST /v0.1/proposals',
        'Binds the request to one offer version; the gateway decides AUTO / ASK / BLOCK.',
      ],
      ['getProposal', 'GET /v0.1/proposals/{proposalId}', 'The current state of a proposal.'],
      ['getAuditTrail', 'GET /v0.1/audit', 'The decision log for one proposal or one customer.'],
      ['getMandate', 'GET /v0.1/mandates/{customerId}', "The buyer's mandate, read-only."],
    ],
  },
  {
    channel: 'Approver channel: the named human only, never an agent tool',
    rows: [
      [
        'approver view',
        'GET /v0.1/approver/proposals/{proposalId}',
        'The exact version to approve, with a single-use demo approval link.',
      ],
      [
        'decideApproval',
        'POST /v0.1/approver/proposals/{proposalId}/decision',
        'APPROVE or REJECT; approval triggers a live re-check.',
      ],
    ],
  },
  {
    channel: 'Buyer admin: a human changes the policy',
    rows: [
      [
        'changeMandate',
        'PATCH /v0.1/admin/mandates/{customerId}',
        'New mandate version; open proposals are re-decided.',
      ],
      [
        'resetMandate',
        'POST /v0.1/admin/mandates/{customerId}/reset',
        'Restores the shipped default as a new version.',
      ],
    ],
  },
]

const EFFECT_WORDS: Record<ReasonDefinition['effect'], string> = {
  ASK: 'Ask a human',
  EXCLUDE: 'Exclude the product',
  BLOCK: 'Block the order',
  INFO: 'Information',
  NO_ORDER: 'No order',
}

const REASONS = Object.values(REASON_REGISTRY) as ReasonDefinition[]
const EFFECT_ORDER: ReasonDefinition['effect'][] = ['ASK', 'EXCLUDE', 'BLOCK', 'INFO', 'NO_ORDER']

function downloadSchema(name: string, schema: unknown) {
  const blob = new Blob([JSON.stringify(schema, null, 2)], { type: 'application/schema+json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${name}.json`
  link.click()
  URL.revokeObjectURL(url)
}

function Section({
  id,
  title,
  children,
}: {
  id: string
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="panel" aria-labelledby={id}>
      <h2 id={id} className="panel-title">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** S7: the human-readable open specification of Agent Trust Labels v0.1 and the gateway interface. */
export function Spec() {
  return (
    <div className="grid min-w-0 grid-cols-1 gap-3 [&>*]:min-w-0">
      <div>
        <a
          href="/"
          aria-label="Back to the purchasing flow"
          className="inline-flex items-center gap-1 text-sm font-semibold text-[var(--green)] underline"
        >
          <ArrowLeft size={15} aria-hidden /> Buy
        </a>
      </div>

      <section className="panel" aria-labelledby="spec-title">
        <h1 id="spec-title" className="text-lg font-bold text-[var(--ink)]">
          Agent Trust Labels v0.1 · open specification
        </h1>
        <p className="muted small mt-1">
          An agent acts alone only when every decisive fact is verified or declared without conflict
          and the order is within mandate; everything else goes to a named human for one proposal
          version.
        </p>
        <nav aria-label="On this page" className="mt-2.5">
          <ul className="flex flex-wrap gap-1.5 text-xs">
            {[
              ['#levels', 'Trust levels'],
              ['#facts', 'Facts'],
              ['#reasons', 'Reason codes'],
              ['#operations', 'Operations'],
              ['#machine-readable', 'OpenAPI & Schemas'],
            ].map(([href, label]) => (
              <li key={href}>
                <a href={href} className="btn btn-scenario log-scenario-pill">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </section>

      <Section id="levels" title="Trust levels">
        <ul className="grid gap-3">
          {LEVELS.map(({ level, meaning, examples }) => (
            <li key={level} className="grid gap-1">
              <span className="flex flex-wrap items-center gap-2">
                <TrustBadge level={level} />
                <span className="font-semibold">{meaning}</span>
              </span>
              <span className="muted small">{examples}</span>
            </li>
          ))}
          <li className="grid gap-1">
            <span className="flex flex-wrap items-center gap-2">
              <TrustBadge level="DECLARED" conflict={{ code: 'K1', message: 'example' }} />
              <span className="font-semibold">
                CONFLICT is a flag that combines with any level: two sources disagree, or a value is
                internally inconsistent.
              </span>
            </span>
          </li>
        </ul>
        <h3 className="mt-4 font-semibold">Display rules</h3>
        <ul className="ml-5 list-disc text-sm">
          <li>
            Every fact shows its value, a trust badge (icon and word), its source, and the time for
            live facts.
          </li>
          <li>Colour is never the only signal.</li>
          <li>UNKNOWN is shown as "Unknown", never blank.</li>
          <li>A conflict shows a one-line explanation.</li>
        </ul>
      </Section>

      <Section id="facts" title="Facts: the trust label">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Meaning</th>
              </tr>
            </thead>
            <tbody>
              {LABEL_FIELDS.map(([field, meaning]) => (
                <tr key={field}>
                  <td>
                    <code>{field}</code>
                  </td>
                  <td>{meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 className="mt-4 font-semibold">Sources</h3>
        <div className="table-scroll">
          <table className="data-table">
            <tbody>
              {SOURCES.map(([system, meaning]) => (
                <tr key={system}>
                  <td>
                    <code>{system}</code>
                  </td>
                  <td>{meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 className="mt-4 font-semibold">Conflicts</h3>
        <p className="muted small">A conflict on a decisive fact never lets the agent act alone.</p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Trigger</th>
                <th scope="col">Reason code</th>
              </tr>
            </thead>
            <tbody>
              {CONFLICTS.map(([code, trigger, reason]) => (
                <tr key={code}>
                  <td>
                    <strong>{code}</strong>
                  </td>
                  <td>{trigger}</td>
                  <td>
                    <code>{reason}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="reasons" title="Reason codes">
        <p className="muted small">
          Humans approve trade-offs only, never missing evidence; everything else excludes the
          product or blocks the order.
        </p>
        {EFFECT_ORDER.map((effect) => {
          const rows = REASONS.filter((r) => r.effect === effect)
          if (rows.length === 0) return null
          return (
            <div key={effect} className="mt-3">
              <h3 className="font-semibold">
                {EFFECT_WORDS[effect]} <span className="muted small">({effect})</span>
              </h3>
              <div className="table-scroll">
                <table className="data-table" aria-label={`Reason codes: ${EFFECT_WORDS[effect]}`}>
                  <thead>
                    <tr>
                      <th scope="col">Code</th>
                      <th scope="col">Chip</th>
                      <th scope="col">Human can approve</th>
                      <th scope="col">Message template</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((reason) => (
                      <tr key={reason.code}>
                        <td>
                          <code>{reason.code}</code>
                        </td>
                        <td>{reason.chipText}</td>
                        <td>
                          {reason.approvable ? (
                            <span className="inline-flex items-center gap-1 text-[var(--auto)]">
                              <CheckCircle2 size={14} aria-hidden /> Yes
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[var(--block)]">
                              <XCircle size={14} aria-hidden /> No
                            </span>
                          )}
                        </td>
                        <td className="small">{reason.template}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        })}
      </Section>

      <Section id="operations" title="Operations">
        <p className="muted small">
          Deterministic gateway policy decides AUTO / ASK / BLOCK; approval links never reach the
          agent. Every confirmation re-checks live first (Simulated order — mock environment).
        </p>
        {OPERATIONS.map(({ channel, rows }) => (
          <div key={channel} className="mt-3">
            <h3 className="font-semibold">{channel}</h3>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Operation</th>
                    <th scope="col">Route</th>
                    <th scope="col">What it does</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([name, route, what]) => (
                    <tr key={name}>
                      <td>
                        <code>{name}</code>
                      </td>
                      <td>
                        <code>{route}</code>
                      </td>
                      <td>{what}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Section>

      <Section id="machine-readable" title="OpenAPI and JSON Schemas">
        <ul className="flex flex-wrap gap-2">
          <li>
            <a href="/openapi.json" className="btn btn-primary">
              OpenAPI 3.1 (JSON)
            </a>
          </li>
          <li>
            <a href="/docs" className="btn btn-scenario">
              Interactive API docs
            </a>
          </li>
        </ul>
        <p className="muted small mt-2.5">
          Draft 2020-12 schemas (<code>urn:verified-or-ask:v0.1:&lt;Model&gt;</code>). Money and
          quantities are decimal strings.
        </p>
        <ul
          className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 [&>*]:min-w-0"
          aria-label="JSON Schemas"
        >
          {SCHEMAS.map(({ name, schema }) => (
            <li key={name}>
              <details className="rounded-md border border-[var(--line)] p-2">
                <summary className="cursor-pointer font-semibold">
                  <FileJson size={14} aria-hidden className="mr-1 inline align-[-2px]" />
                  {name}
                </summary>
                <button
                  type="button"
                  className="btn mt-2 inline-flex items-center gap-1"
                  onClick={() => downloadSchema(name, schema)}
                >
                  <Download size={14} aria-hidden /> Download {name}.json
                </button>
                <pre className="mt-2 max-h-64 max-w-full overflow-auto text-xs">
                  {JSON.stringify(schema, null, 2)}
                </pre>
              </details>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
