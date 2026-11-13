export type Product = {
  code: string
  name: string
  category: string
  unit: 'L' | 'KG' | 'EA'
  pack_quantity: string
  dietary: Record<string, boolean | null>
  allergens: string[] | null
  reliability: number | null
  carbon_kg_co2_per_kg: number | null
  source: string
}
export type Line = { product_code: string; quantity: string; unit: string; substitute_for?: string }
export type OfferLine = {
  product_code: string
  product_name: string
  quantity: string
  unit: string
  pack_quantity: string
  unit_price: string
  line_total: string
  available: boolean
  available_quantity: string
  earliest_delivery: string
  source: string
  verified_at: string
}
export type Offer = {
  items: OfferLine[]
  total: string
  currency: string
  mock_data: boolean
  delivery_date: string
  disclaimer: string
  price_basis: string
}
export type ProposalRequest = {
  items: Line[]
  delivery_date: string
  request: string
  budget: string | null
  excluded_allergens: string[]
  required_dietary: string[]
}
export type Proposal = {
  id: string
  buyer_id: string
  buyer_name: string
  status: 'AUTO_APPROVED' | 'NEEDS_APPROVAL' | 'APPROVED' | 'REJECTED' | 'COMMITTED'
  request: ProposalRequest
  offer: Offer
  reasons: { code: string; message: string }[]
  policy_version: number
  approved_by: string | null
  created_at: string
  order: { id: string; total: string; simulated: boolean; source: string } | null
}
export type Policy = {
  buyer_id: string
  name: string
  version: number
  max_auto_total: string
  allowed_categories: string[]
  allow_equivalent_substitutions: boolean
  excluded_allergens: string[]
  unusual_quantity: string
}
export type AuditEvent = {
  id: number
  proposal_id: string | null
  buyer_id: string
  actor: string
  event: string
  summary: string
  data: Record<string, unknown>
  created_at: string
}
export type Context = {
  today: string
  next_monday: string
  timezone: string
  mock_data: boolean
  simulated_orders: boolean
  ai_configured: boolean
}
export type ChatResult = {
  message: string
  mode: 'ai' | 'guided'
  proposal: Proposal | null
  tool_results: { tool: string; result: Record<string, unknown> }[]
}
