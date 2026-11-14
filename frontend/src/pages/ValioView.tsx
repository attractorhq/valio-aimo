import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  Database,
  Download,
  Filter,
  RefreshCw,
  ShieldAlert,
  Wrench,
} from 'lucide-react'
import {
  REASON_REGISTRY,
  type AuditTrail,
  type DecisionOutcome,
  type Product,
  type Proposal,
  type ReasonCode,
  type ReasonEffect,
} from '../lib/contracts'
import { adminHeaders, agentHeaders } from '../lib/demoAccess'
import { formatMoney, formatTime, reasonChip } from '../lib/format'
import { gateway } from '../lib/gateway'
import { LoadingSection, Skeleton } from '../components/Loading'
import { ALLOW_MOCK } from '../lib/env'
import scenarioA from '../mocks/scenario-a.json'
import scenarioB from '../mocks/scenario-b.json'
import scenarioC from '../mocks/scenario-c.json'
import { ValioAgentActivity } from './ValioAgentActivity'

export type DataGapMetric = {
  gapId: string
  title: string
  reasonCode: ReasonCode
  rule: string
  effect: 'EXCLUDE' | 'ASK'
  blocksAuto: boolean
  approvableByHuman: boolean
  affectedProducts: number
  affectedSuppliers: number
  shareOfCatalog: string
  fixAction: string
}

export type SupplierReadiness = {
  supplierName: string
  totalProducts: number
  foodProducts: number
  nonFoodProducts: number
  autoReadyProducts: number
  blockedFromAutoProducts: number
  autoReadyRate: string
  missingAllergensFood: number
  contentUnknown: number
  contentWeakNameEstimate: number
  dataConflicts: number
  unpricedDeposits: number
  unsafeSubstitutes: number
  lowReliability: number
  blockingReasonCodes: ReasonCode[]
  topFix: string
}

export type FixListItem = {
  fixId: string
  priority: 'HIGH' | 'MEDIUM'
  scope: 'SUPPLIER' | 'PRODUCT'
  supplierName: string
  productId?: string | null
  productName?: string | null
  reasonCode: ReasonCode
  effect: 'EXCLUDE' | 'ASK'
  blocksAuto: boolean
  problem: string
  fixAction: string
  productsUnlocked: number
}

export type ReasonFrequency = {
  code: ReasonCode
  effect: ReasonEffect
  approvable: boolean
  chipText: string
  occurrences: number
}

export type DecisionStatistics = {
  totalProposals: number
  activeProposals: number
  supersededProposals: number
  confirmedSimulated: number
  pendingApproval: number
  rejected: number
  expired: number
  blocked: number
  byDecision: Record<DecisionOutcome, number>
  byDecisionActive: Record<DecisionOutcome, number>
  autoRate: string
  askRate: string
  blockRate: string
  totalOrderValueExclVat: string
  autoValueExclVat: string
  askValueExclVat: string
  blockValueExclVat: string
  reasonCounts: ReasonFrequency[]
  excludedReasonCounts: ReasonFrequency[]
  totalExcludedCandidates: number
}

export type CatalogReadinessSummary = {
  totalProducts: number
  totalSuppliers: number
  foodProducts: number
  nonFoodProducts: number
  autoReadyProducts: number
  blockedFromAutoProducts: number
  autoReadyRate: string
  foodAutoReadyProducts: number
  foodBlockedFromAutoProducts: number
  foodAutoReadyRate: string
  missingAllergensFood: number
  missingAllergensTotal: number
  onlyUndeclaredAllergens: number
  x99Products: number
  implicitOnlyAllergensFood: number
  contentDeclared: number
  contentPriceRatioEstimate: number
  contentWeakNameEstimate: number
  contentUnknown: number
  dataConflicts: number
  conflictsByCode: Record<string, number>
  unpricedDeposits: number
  unsafeSubstitutes: number
  reliabilityKnown: number
  reliabilityUnknown: number
  lowReliabilityUnder95: number
  co2Known: number
  co2Unknown: number
}

export type ReadinessInsights = {
  mode: 'LIVE' | 'RECORDED'
  catalogSummary: CatalogReadinessSummary
  gapBreakdown: DataGapMetric[]
  suppliers: SupplierReadiness[]
  fixList: FixListItem[]
  decisionStats: DecisionStatistics
}

const SAFETY_CLAIMS = new Set(['FREE_FROM_GLUTEN', 'FREE_FROM_LACTOSE', 'VEGAN', 'VEGETARIAN'])

function formatRate(numerator: number, denominator: number): string {
  if (denominator <= 0 || numerator <= 0) return '0'
  return (numerator / denominator).toFixed(4).replace(/\.?0+$/, '') || '0'
}

export function formatPercent(rateStr: string | number): string {
  const num = typeof rateStr === 'number' ? rateStr : Number(rateStr)
  if (!Number.isFinite(num) || num <= 0) return '0.0%'
  return `${(num * 100).toFixed(1)}%`
}

function inferSupplierFromName(name: string): string {
  const withoutMult = (name || '').trim().replace(/^\s*\d+\s*[xX*]\s*/, '')
  const withoutQty = withoutMult.replace(/^\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l|dl|cl|ml|pcs|st)\b\s*/i, '').trim()
  const cleaned = withoutQty.replace(/[^\w\s®™&-]/g, ' ').trim()
  if (!cleaned) return 'Unknown supplier'
  const tokens = cleaned.split(/\s+/)
  const first = tokens[0].replace(/[®™]/g, '')
  if (!first) return 'Unknown supplier'
  if ((first.toLowerCase() === 'synthetic' || first.toLowerCase() === 'mock') && tokens.length >= 2) {
    return `${first} ${tokens[1]}`
  }
  return first.toUpperCase()
}

function productConflictCodes(product: Product): string[] {
  const codes = new Set<string>()
  for (const fact of product.facts ?? []) {
    if (fact.conflict?.code) codes.add(fact.conflict.code)
  }
  for (const decl of product.allergens ?? []) {
    if (decl.evidence?.conflict?.code) codes.add(decl.evidence.conflict.code)
  }
  const explicit = new Map<string, string>()
  for (const decl of product.allergens ?? []) {
    explicit.set(decl.code, decl.status)
  }
  if ((product.claims ?? []).includes('FREE_FROM_GLUTEN') && explicit.get('AW') === 'CONTAINS') {
    codes.add('K1')
  }
  if ((product.claims ?? []).includes('FREE_FROM_LACTOSE') && explicit.get('ML') === 'CONTAINS') {
    codes.add('K2')
  }
  return [...codes].sort()
}

function productReliability(product: Product): number | null {
  for (const fact of product.facts ?? []) {
    if (fact.fact === 'reliability' && fact.value !== null && fact.value !== undefined) {
      const n = Number(fact.value)
      if (Number.isFinite(n)) return n
    }
  }
  return null
}

function findUnsafeSubstitutes(
  product: Product,
  productsById: ReadonlyMap<string, Product>,
): Array<{ substitute: Product; broken: string[] }> {
  const subs = product.substitutions ?? []
  if (!subs.length) return []
  const ownClaims = new Set(product.claims ?? [])
  const ownFreeAllergens = new Set(
    (product.allergens ?? [])
      .filter((a) => a.status === 'FREE_FROM' && a.code !== 'X99')
      .map((a) => a.code),
  )
  const out: Array<{ substitute: Product; broken: string[] }> = []

  for (const subId of subs) {
    const sub = productsById.get(subId)
    if (!sub) continue
    const subClaims = new Set(sub.claims ?? [])
    const broken: string[] = []
    for (const c of [...ownClaims].sort()) {
      if (!subClaims.has(c)) broken.push(`missing ${c}`)
    }
    const subAlg = new Map((sub.allergens ?? []).map((a) => [a.code, a.status]))
    for (const code of [...ownFreeAllergens].sort()) {
      const st = subAlg.get(code)
      if (st === 'CONTAINS' || st === 'MAY_CONTAIN') {
        broken.push(`allergen ${code} is ${st}`)
      }
    }
    if (broken.length > 0) {
      out.push({ substitute: sub, broken })
    }
  }
  return out
}

function supplierTopFix(st: {
  missingAllergensFood: number
  contentUnknown: number
  contentWeakNameEstimate: number
  dataConflicts: number
  unpricedDeposits: number
  unsafeSubstitutes: number
  lowReliability: number
}): string {
  const candidates: Array<[number, string]> = []
  if (st.missingAllergensFood > 0) {
    candidates.push([
      st.missingAllergensFood,
      `Declare GS1 Synkka allergen / nonAllergen classifications for ${st.missingAllergensFood} food product(s) (eliminates ALLERGEN_UNKNOWN exclusions).`,
    ])
  }
  if (st.contentUnknown > 0) {
    candidates.push([
      st.contentUnknown,
      `Populate synkkaData.unitConversions[BASE_UNIT_OR_EACH] volume or netWeight for ${st.contentUnknown} product(s) with unknown pack size (eliminates QUANTITY_BASIS_UNKNOWN exclusions).`,
    ])
  }
  if (st.contentWeakNameEstimate > 0) {
    candidates.push([
      st.contentWeakNameEstimate,
      `Add structured synkkaData.unitConversions[BASE_UNIT_OR_EACH] volume/netWeight for ${st.contentWeakNameEstimate} name-sized product(s) (upgrades QUANTITY_BASIS_WEAK from ASK to AUTO).`,
    ])
  }
  if (st.unpricedDeposits > 0) {
    candidates.push([
      st.unpricedDeposits,
      `Supply verified deposit unit prices for ${st.unpricedDeposits} product(s) (eliminates DEPOSIT_COST_UNVERIFIED exclusions).`,
    ])
  }
  if (st.dataConflicts > 0) {
    candidates.push([
      st.dataConflicts,
      `Reconcile contradictory allergen vs claim declarations on ${st.dataConflicts} product(s) (eliminates DATA_CONFLICT exclusions).`,
    ])
  }
  if (st.unsafeSubstitutes > 0) {
    candidates.push([
      st.unsafeSubstitutes,
      `Remove or replace ${st.unsafeSubstitutes} non-compliant Valio-listed substitute link(s) (eliminates SUBSTITUTE_REJECTED exclusions).`,
    ])
  }
  if (st.lowReliability > 0) {
    candidates.push([
      st.lowReliability,
      `Improve fulfilment reliability ≥ 95% on ${st.lowReliability} product(s) (eliminates LOW_RELIABILITY human sign-off).`,
    ])
  }
  if (!candidates.length) {
    return 'All static product facts are declared and consistent for autonomous ordering.'
  }
  candidates.sort((a, b) => b[0] - a[0])
  return candidates[0][1]
}

/** Compute live AUTO / ASK / BLOCK statistics directly from a list of Proposal snapshots. */
export function computeDecisionStatisticsFromProposals(proposals: readonly Proposal[]): DecisionStatistics {
  const total = proposals.length
  const active = proposals.filter((p) => p.state !== 'SUPERSEDED')
  const supersededProposals = total - active.length

  const confirmedSimulated = proposals.filter((p) => p.state === 'CONFIRMED_SIMULATED').length
  const pendingApproval = proposals.filter((p) => p.state === 'PENDING_APPROVAL').length
  const rejected = proposals.filter((p) => p.state === 'REJECTED').length
  const expired = proposals.filter((p) => p.state === 'EXPIRED').length
  const blocked = proposals.filter((p) => p.state === 'BLOCKED').length

  const byDecision: Record<DecisionOutcome, number> = { AUTO: 0, ASK: 0, BLOCK: 0 }
  const byDecisionActive: Record<DecisionOutcome, number> = { AUTO: 0, ASK: 0, BLOCK: 0 }
  for (const p of proposals) {
    byDecision[p.decision] = (byDecision[p.decision] ?? 0) + 1
  }
  for (const p of active) {
    byDecisionActive[p.decision] = (byDecisionActive[p.decision] ?? 0) + 1
  }

  const sumCents = (items: readonly Proposal[]) =>
    items.reduce((acc, p) => acc + Math.round(Number(p.totals.exclVat) * 100), 0)

  const centsToMoney = (cents: number) => (cents / 100).toFixed(2)

  const orderReasons = new Map<ReasonCode, number>()
  const excludedReasons = new Map<ReasonCode, number>()
  let totalExcludedCandidates = 0

  for (const p of proposals) {
    const seen = new Set<ReasonCode>(p.reasons ?? [])
    for (const line of p.lines ?? []) {
      for (const code of line.reasons ?? []) seen.add(code)
    }
    for (const code of seen) {
      orderReasons.set(code, (orderReasons.get(code) ?? 0) + 1)
    }
    for (const ex of p.excluded ?? []) {
      totalExcludedCandidates += 1
      for (const code of new Set(ex.reasons ?? [])) {
        excludedReasons.set(code, (excludedReasons.get(code) ?? 0) + 1)
      }
    }
  }

  const buildFreq = (map: Map<ReasonCode, number>): ReasonFrequency[] =>
    [...map.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([code, occurrences]) => {
        const reg = REASON_REGISTRY[code]
        return {
          code,
          effect: reg.effect,
          approvable: reg.approvable,
          chipText: reg.chipText,
          occurrences,
        }
      })

  return {
    totalProposals: total,
    activeProposals: active.length,
    supersededProposals,
    confirmedSimulated,
    pendingApproval,
    rejected,
    expired,
    blocked,
    byDecision,
    byDecisionActive,
    autoRate: formatRate(byDecision.AUTO, total),
    askRate: formatRate(byDecision.ASK, total),
    blockRate: formatRate(byDecision.BLOCK, total),
    totalOrderValueExclVat: centsToMoney(sumCents(active)),
    autoValueExclVat: centsToMoney(sumCents(active.filter((p) => p.decision === 'AUTO'))),
    askValueExclVat: centsToMoney(sumCents(active.filter((p) => p.decision === 'ASK'))),
    blockValueExclVat: centsToMoney(sumCents(active.filter((p) => p.decision === 'BLOCK'))),
    reasonCounts: buildFreq(orderReasons),
    excludedReasonCounts: buildFreq(excludedReasons),
    totalExcludedCandidates,
  }
}

/** Pure computation of ReadinessInsights from normalised Product[] and Proposal[] data. */
export function computeInsightsFromData(
  products: readonly Product[],
  proposals: readonly Proposal[],
  vendorByProductId: Readonly<Record<string, string>> = {},
  mode: 'LIVE' | 'RECORDED' = 'RECORDED',
): ReadinessInsights {
  const productsById = new Map(products.map((p) => [p.productId, p]))
  const totalProducts = products.length

  let foodProducts = 0
  let nonFoodProducts = 0
  let autoReadyProducts = 0
  let foodAutoReadyProducts = 0

  let missingAllergensFood = 0
  let missingAllergensTotal = 0
  let onlyUndeclaredAllergens = 0
  let x99Products = 0
  let implicitOnlyAllergensFood = 0

  let contentDeclared = 0
  let contentPriceRatioEstimate = 0
  let contentWeakNameEstimate = 0
  let contentUnknown = 0

  let dataConflicts = 0
  const conflictsByCode: Record<string, number> = {}
  let unpricedDeposits = 0
  let unsafeSubstitutes = 0

  let reliabilityKnown = 0
  let lowReliabilityUnder95 = 0
  let co2Known = 0

  const gapSuppliers = new Map<string, Set<string>>()
  const markGapSupplier = (gapId: string, supplier: string) => {
    let set = gapSuppliers.get(gapId)
    if (!set) {
      set = new Set<string>()
      gapSuppliers.set(gapId, set)
    }
    set.add(supplier)
  }

  const bySupplier = new Map<
    string,
    {
      total: number
      food: number
      nonFood: number
      autoReady: number
      blockedFromAuto: number
      missingAllergensFood: number
      contentUnknown: number
      contentWeakNameEstimate: number
      dataConflicts: number
      unpricedDeposits: number
      unsafeSubstitutes: number
      lowReliability: number
    }
  >()

  const conflictProducts: Array<{ product: Product; supplier: string; codes: string[] }> = []
  const unsafeSubProducts: Array<{
    product: Product
    supplier: string
    substitute: Product
    broken: string[]
  }> = []
  const gfMissingAlgProducts: Array<{ product: Product; supplier: string }> = []

  for (const product of products) {
    const supplier = (vendorByProductId[product.productId] || '').trim() || inferSupplierFromName(product.name)
    let supStats = bySupplier.get(supplier)
    if (!supStats) {
      supStats = {
        total: 0,
        food: 0,
        nonFood: 0,
        autoReady: 0,
        blockedFromAuto: 0,
        missingAllergensFood: 0,
        contentUnknown: 0,
        contentWeakNameEstimate: 0,
        dataConflicts: 0,
        unpricedDeposits: 0,
        unsafeSubstitutes: 0,
        lowReliability: 0,
      }
      bySupplier.set(supplier, supStats)
    }
    supStats.total += 1

    const isFood = product.isFood !== false
    if (isFood) {
      foodProducts += 1
      supStats.food += 1
    } else {
      nonFoodProducts += 1
      supStats.nonFood += 1
    }

    if (!product.hasAllergenDeclaration) {
      missingAllergensTotal += 1
      if ((product.allergens ?? []).length > 0 && (product.allergens ?? []).every((a) => a.status === 'UNDECLARED')) {
        onlyUndeclaredAllergens += 1
      }
      if (isFood) {
        missingAllergensFood += 1
        supStats.missingAllergensFood += 1
        markGapSupplier('MISSING_ALLERGEN_DECLARATION', supplier)
        if ((product.claims ?? []).some((c) => SAFETY_CLAIMS.has(c))) {
          gfMissingAlgProducts.push({ product, supplier })
        }
      }
    } else {
      const freeCodes = new Set(
        (product.allergens ?? []).filter((a) => a.status === 'FREE_FROM').map((a) => a.code),
      )
      if (freeCodes.has('X99')) x99Products += 1
      if (isFood && freeCodes.size === 0) {
        implicitOnlyAllergensFood += 1
        markGapSupplier('IMPLICIT_ONLY_ALLERGENS', supplier)
      }
    }

    const cpsu = product.contentPerSalesUnit
    const isContentUnknown = !cpsu
    const isContentWeak = Boolean(cpsu && cpsu.method === 'name')
    if (isContentUnknown) {
      contentUnknown += 1
      supStats.contentUnknown += 1
      markGapSupplier('CONTENT_UNKNOWN', supplier)
    } else if (isContentWeak) {
      contentWeakNameEstimate += 1
      supStats.contentWeakNameEstimate += 1
      markGapSupplier('CONTENT_NAME_ONLY_WEAK', supplier)
    } else if (cpsu?.method === 'catalogPrice.value / comparisonPrice.value') {
      contentPriceRatioEstimate += 1
    } else {
      contentDeclared += 1
    }

    const cCodes = productConflictCodes(product)
    const hasConflict = cCodes.length > 0
    if (hasConflict) {
      dataConflicts += 1
      supStats.dataConflicts += 1
      markGapSupplier('DATA_CONFLICT', supplier)
      for (const code of cCodes) {
        conflictsByCode[code] = (conflictsByCode[code] ?? 0) + 1
      }
      conflictProducts.push({ product, supplier, codes: cCodes })
    }

    const hasUnpricedDeposit = (product.deposits ?? []).some(
      (d) => d.unitPriceExclVat === null || d.unitPriceExclVat === undefined,
    )
    if (hasUnpricedDeposit) {
      unpricedDeposits += 1
      supStats.unpricedDeposits += 1
      markGapSupplier('UNPRICED_DEPOSIT', supplier)
    }

    const unsafeSubs = findUnsafeSubstitutes(product, productsById)
    if (unsafeSubs.length > 0) {
      unsafeSubstitutes += 1
      supStats.unsafeSubstitutes += 1
      markGapSupplier('UNSAFE_VALIO_SUBSTITUTE', supplier)
      for (const item of unsafeSubs) {
        unsafeSubProducts.push({
          product,
          supplier,
          substitute: item.substitute,
          broken: item.broken,
        })
      }
    }

    const rel = productReliability(product)
    let hasLowRel = false
    if (rel !== null) {
      reliabilityKnown += 1
      if (rel < 0.95) {
        lowReliabilityUnder95 += 1
        hasLowRel = true
        supStats.lowReliability += 1
        markGapSupplier('LOW_RELIABILITY', supplier)
      }
    }
    if ((product.facts ?? []).some((f) => f.fact === 'co2' && f.value !== null && f.value !== undefined)) {
      co2Known += 1
    }

    const blocksAuto =
      (isFood && !product.hasAllergenDeclaration) ||
      isContentUnknown ||
      isContentWeak ||
      hasConflict ||
      hasUnpricedDeposit ||
      hasLowRel

    if (blocksAuto) {
      supStats.blockedFromAuto += 1
    } else {
      autoReadyProducts += 1
      supStats.autoReady += 1
      if (isFood) foodAutoReadyProducts += 1
    }
  }

  const catalogSummary: CatalogReadinessSummary = {
    totalProducts,
    totalSuppliers: bySupplier.size,
    foodProducts,
    nonFoodProducts,
    autoReadyProducts,
    blockedFromAutoProducts: totalProducts - autoReadyProducts,
    autoReadyRate: formatRate(autoReadyProducts, totalProducts),
    foodAutoReadyProducts,
    foodBlockedFromAutoProducts: foodProducts - foodAutoReadyProducts,
    foodAutoReadyRate: formatRate(foodAutoReadyProducts, foodProducts),
    missingAllergensFood,
    missingAllergensTotal,
    onlyUndeclaredAllergens,
    x99Products,
    implicitOnlyAllergensFood,
    contentDeclared,
    contentPriceRatioEstimate,
    contentWeakNameEstimate,
    contentUnknown,
    dataConflicts,
    conflictsByCode,
    unpricedDeposits,
    unsafeSubstitutes,
    reliabilityKnown,
    reliabilityUnknown: totalProducts - reliabilityKnown,
    lowReliabilityUnder95,
    co2Known,
    co2Unknown: totalProducts - co2Known,
  }

  const gapBreakdown: DataGapMetric[] = (
    [
      {
        gapId: 'CONTENT_NAME_ONLY_WEAK',
        title: 'Pack content parsed only from product name (Method 3)',
        reasonCode: 'QUANTITY_BASIS_WEAK' as ReasonCode,
        rule: 'L12 / P2.2',
        effect: 'ASK' as const,
        blocksAuto: true,
        approvableByHuman: true,
        affectedProducts: contentWeakNameEstimate,
        affectedSuppliers: gapSuppliers.get('CONTENT_NAME_ONLY_WEAK')?.size ?? 0,
        shareOfCatalog: formatRate(contentWeakNameEstimate, totalProducts),
        fixAction:
          'Populate synkkaData.unitConversions[synkkaPackagingType=BASE_UNIT_OR_EACH].volume or .netWeight and units[salesUnit].sizeInBaseUnits so sizing is DECLARED (Method 1) instead of a name estimate.',
      },
      {
        gapId: 'MISSING_ALLERGEN_DECLARATION',
        title: 'Food products with no allergen declaration',
        reasonCode: 'ALLERGEN_UNKNOWN' as ReasonCode,
        rule: 'L4 / P2.3',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: missingAllergensFood,
        affectedSuppliers: gapSuppliers.get('MISSING_ALLERGEN_DECLARATION')?.size ?? 0,
        shareOfCatalog: formatRate(missingAllergensFood, totalProducts),
        fixAction:
          'Require GS1 Synkka classifications[allergen / nonAllergen] (or X99 for EU-14 allergen-free products) from the supplier; missing allergen evidence is never approvable by a human.',
      },
      {
        gapId: 'CONTENT_UNKNOWN',
        title: 'Pack content per sales unit completely unknown',
        reasonCode: 'QUANTITY_BASIS_UNKNOWN' as ReasonCode,
        rule: 'L12 / P2.3',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: contentUnknown,
        affectedSuppliers: gapSuppliers.get('CONTENT_UNKNOWN')?.size ?? 0,
        shareOfCatalog: formatRate(contentUnknown, totalProducts),
        fixAction:
          'Provide BASE_UNIT_OR_EACH volume/netWeight in synkkaData.unitConversions or comparisonPrice/unit so physical-unit requests (kg / l) can be sized into sales units.',
      },
      {
        gapId: 'IMPLICIT_ONLY_ALLERGENS',
        title: 'Food allergens declared only by omission (no explicit FREE_FROM or X99)',
        reasonCode: 'ALLERGEN_IMPLICIT' as ReasonCode,
        rule: 'L4 / P2.4',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: implicitOnlyAllergensFood,
        affectedSuppliers: gapSuppliers.get('IMPLICIT_ONLY_ALLERGENS')?.size ?? 0,
        shareOfCatalog: formatRate(implicitOnlyAllergensFood, totalProducts),
        fixAction:
          'Add explicit nonAllergen FREE_FROM declarations (or X99 for EU-14 codes) so STRICT-mandate buyers (schools, care kitchens) can order autonomously.',
      },
      {
        gapId: 'LOW_RELIABILITY',
        title: 'Historical delivery reliability below 95% threshold',
        reasonCode: 'LOW_RELIABILITY' as ReasonCode,
        rule: 'L13 / P2.2',
        effect: 'ASK' as const,
        blocksAuto: true,
        approvableByHuman: true,
        affectedProducts: lowReliabilityUnder95,
        affectedSuppliers: gapSuppliers.get('LOW_RELIABILITY')?.size ?? 0,
        shareOfCatalog: formatRate(lowReliabilityUnder95, totalProducts),
        fixAction:
          'Address supplier fulfilment bottlenecks or buffer stock in Valio Aimo warehouses so historical delivery reliability meets buyer mandate thresholds (≥ 95% / ≥ 98%).',
      },
      {
        gapId: 'UNPRICED_DEPOSIT',
        title: 'Returnable deposit material without a verified unit price',
        reasonCode: 'DEPOSIT_COST_UNVERIFIED' as ReasonCode,
        rule: 'L3 / P2.3',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: unpricedDeposits,
        affectedSuppliers: gapSuppliers.get('UNPRICED_DEPOSIT')?.size ?? 0,
        shareOfCatalog: formatRate(unpricedDeposits, totalProducts),
        fixAction:
          'Publish unitPriceExclVat for every deposit material in the price catalogue so order totals including returnable packaging are complete and verifiable.',
      },
      {
        gapId: 'UNSAFE_VALIO_SUBSTITUTE',
        title: 'Valio-listed substitute breaks a claim or allergen proof of the primary product',
        reasonCode: 'SUBSTITUTE_REJECTED' as ReasonCode,
        rule: 'L11 / P8',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: unsafeSubstitutes,
        affectedSuppliers: gapSuppliers.get('UNSAFE_VALIO_SUBSTITUTE')?.size ?? 0,
        shareOfCatalog: formatRate(unsafeSubstitutes, totalProducts),
        fixAction:
          'Audit substitutions[].substituteProduct links against nutritionalClaim and nonAllergen FREE_FROM declarations so listed substitutes preserve gluten-free, lactose-free and vegan guarantees.',
      },
      {
        gapId: 'DATA_CONFLICT',
        title: 'Contradictory supplier declarations (K1/K2/K5/K6)',
        reasonCode: 'DATA_CONFLICT' as ReasonCode,
        rule: 'L9 / P2.3',
        effect: 'EXCLUDE' as const,
        blocksAuto: true,
        approvableByHuman: false,
        affectedProducts: dataConflicts,
        affectedSuppliers: gapSuppliers.get('DATA_CONFLICT')?.size ?? 0,
        shareOfCatalog: formatRate(dataConflicts, totalProducts),
        fixAction:
          'Validate GS1 Synkka imports to reject FREE_FROM_GLUTEN + AW CONTAINS (K1), FREE_FROM_LACTOSE + ML CONTAINS (K2), and dairy products marked FREE_FROM AM (K6).',
      },
    ] as DataGapMetric[]
  ).sort((a, b) => b.affectedProducts - a.affectedProducts || a.gapId.localeCompare(b.gapId))

  const suppliers: SupplierReadiness[] = [...bySupplier.entries()]
    .map(([supplierName, st]) => {
      const codes: ReasonCode[] = []
      if (st.missingAllergensFood > 0) codes.push('ALLERGEN_UNKNOWN')
      if (st.contentUnknown > 0) codes.push('QUANTITY_BASIS_UNKNOWN')
      if (st.contentWeakNameEstimate > 0) codes.push('QUANTITY_BASIS_WEAK')
      if (st.dataConflicts > 0) codes.push('DATA_CONFLICT')
      if (st.unpricedDeposits > 0) codes.push('DEPOSIT_COST_UNVERIFIED')
      if (st.unsafeSubstitutes > 0) codes.push('SUBSTITUTE_REJECTED')
      if (st.lowReliability > 0) codes.push('LOW_RELIABILITY')

      return {
        supplierName,
        totalProducts: st.total,
        foodProducts: st.food,
        nonFoodProducts: st.nonFood,
        autoReadyProducts: st.autoReady,
        blockedFromAutoProducts: st.blockedFromAuto,
        autoReadyRate: formatRate(st.autoReady, st.total),
        missingAllergensFood: st.missingAllergensFood,
        contentUnknown: st.contentUnknown,
        contentWeakNameEstimate: st.contentWeakNameEstimate,
        dataConflicts: st.dataConflicts,
        unpricedDeposits: st.unpricedDeposits,
        unsafeSubstitutes: st.unsafeSubstitutes,
        lowReliability: st.lowReliability,
        blockingReasonCodes: codes,
        topFix: supplierTopFix(st),
      }
    })
    .sort(
      (a, b) =>
        b.blockedFromAutoProducts - a.blockedFromAutoProducts ||
        b.foodProducts - a.foodProducts ||
        b.totalProducts - a.totalProducts ||
        a.supplierName.localeCompare(b.supplierName),
    )

  const fixList: FixListItem[] = []
  suppliers
    .filter((s) => s.blockedFromAutoProducts > 0)
    .slice(0, 10)
    .forEach((sup, idx) => {
      const primaryReason = sup.blockingReasonCodes[0] ?? 'QUANTITY_BASIS_WEAK'
      const effect: 'EXCLUDE' | 'ASK' =
        primaryReason === 'QUANTITY_BASIS_WEAK' || primaryReason === 'LOW_RELIABILITY' ? 'ASK' : 'EXCLUDE'
      const parts: string[] = []
      if (sup.missingAllergensFood > 0) {
        parts.push(`${sup.missingAllergensFood} food product(s) lack allergen declarations`)
      }
      if (sup.contentWeakNameEstimate > 0) {
        parts.push(`${sup.contentWeakNameEstimate} product(s) rely on name-parsed pack size`)
      }
      if (sup.contentUnknown > 0) {
        parts.push(`${sup.contentUnknown} product(s) have no pack content size`)
      }
      if (sup.unpricedDeposits > 0) {
        parts.push(`${sup.unpricedDeposits} product(s) have unpriced deposits`)
      }
      if (sup.dataConflicts > 0) {
        parts.push(`${sup.dataConflicts} product(s) have data conflicts`)
      }
      if (sup.unsafeSubstitutes > 0) {
        parts.push(`${sup.unsafeSubstitutes} product(s) list non-compliant substitutes`)
      }
      fixList.push({
        fixId: `FIX-SUP-${String(idx + 1).padStart(2, '0')}`,
        priority: sup.foodProducts > 0 && sup.blockedFromAutoProducts >= 2 ? 'HIGH' : 'MEDIUM',
        scope: 'SUPPLIER',
        supplierName: sup.supplierName,
        productId: null,
        productName: null,
        reasonCode: primaryReason,
        effect,
        blocksAuto: true,
        problem: `${sup.blockedFromAutoProducts} of ${sup.totalProducts} products from ${sup.supplierName} cannot be AUTO-ordered (${parts.join('; ')}).`,
        fixAction: sup.topFix,
        productsUnlocked: sup.blockedFromAutoProducts,
      })
    })

  unsafeSubProducts.slice(0, 8).forEach((entry, idx) => {
    fixList.push({
      fixId: `FIX-SUB-${String(idx + 1).padStart(2, '0')}`,
      priority: 'HIGH',
      scope: 'PRODUCT',
      supplierName: entry.supplier,
      productId: entry.product.productId,
      productName: entry.product.name,
      reasonCode: 'SUBSTITUTE_REJECTED',
      effect: 'EXCLUDE',
      blocksAuto: true,
      problem: `${entry.product.productId} (${entry.product.name}) lists ${entry.substitute.productId} (${entry.substitute.name}) in substitutions[], but ${entry.substitute.productId} fails: ${entry.broken.join(', ')}.`,
      fixAction: `Replace substitute ${entry.substitute.productId} on ${entry.product.productId} with a substitute that preserves ${(entry.product.claims ?? []).join(', ') || 'all hard requirements'}.`,
      productsUnlocked: 1,
    })
  })

  conflictProducts.slice(0, 8).forEach((entry, idx) => {
    fixList.push({
      fixId: `FIX-CONFLICT-${String(idx + 1).padStart(2, '0')}`,
      priority: 'HIGH',
      scope: 'PRODUCT',
      supplierName: entry.supplier,
      productId: entry.product.productId,
      productName: entry.product.name,
      reasonCode: 'DATA_CONFLICT',
      effect: 'EXCLUDE',
      blocksAuto: true,
      problem: `${entry.product.productId} (${entry.product.name}) has contradictory GS1 Synkka declarations (${entry.codes.join(', ')}).`,
      fixAction: `Reconcile synkkaData.classifications[allergen/nonAllergen] and nutritionalClaim on ${entry.product.productId} to resolve conflict ${entry.codes.join(', ')}.`,
      productsUnlocked: 1,
    })
  })

  gfMissingAlgProducts.slice(0, 6).forEach((entry, idx) => {
    fixList.push({
      fixId: `FIX-ALG-${String(idx + 1).padStart(2, '0')}`,
      priority: 'HIGH',
      scope: 'PRODUCT',
      supplierName: entry.supplier,
      productId: entry.product.productId,
      productName: entry.product.name,
      reasonCode: 'ALLERGEN_UNKNOWN',
      effect: 'EXCLUDE',
      blocksAuto: true,
      problem: `${entry.product.productId} (${entry.product.name}) declares ${(entry.product.claims ?? []).join(', ')} in nutritionalClaim but has zero allergen/nonAllergen declarations (excluded in STRICT mode).`,
      fixAction: `Add explicit synkkaData.classifications[nonAllergen] FREE_FROM declarations on ${entry.product.productId} so STRICT kitchens can order it autonomously.`,
      productsUnlocked: 1,
    })
  })

  return {
    mode,
    catalogSummary,
    gapBreakdown,
    suppliers,
    fixList,
    decisionStats: computeDecisionStatisticsFromProposals(proposals),
  }
}

/** Hand-made synthetic catalogue records used only when the backend `/v0.1/insights` route is offline. */
export const SYNTHETIC_VENDOR_MAP: Readonly<Record<string, string>> = {
  'MOCK-MILK-464306': 'SYNTHETIC DAIRY OY',
  'MOCK-OAT-GF': 'SYNTHETIC DAIRY OY',
  'MOCK-BARISTA-CONFLICT': 'SYNTHETIC DAIRY OY',
  'MOCK-OAT-SUB': 'SYNTHETIC OAT AB',
  'MOCK-PASTA-GF': 'SYNTHETIC PASTA SRL',
  'MOCK-PASTA-UNDECLARED': 'SYNTHETIC NORDIC PASTA',
  'MOCK-PASTA-NAME-ONLY': 'SYNTHETIC NORDIC PASTA',
  'MOCK-CLEANING-UNKNOWN': 'SYNTHETIC NONFOOD SUPPLIES',
}

export const SYNTHETIC_CATALOG_PRODUCTS: readonly Product[] = [
  {
    productId: 'MOCK-MILK-464306',
    name: 'Synthetic lactose-free milk drink 1 l',
    category: '10400',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '1',
      unit: 'l',
      level: 'DECLARED',
      method: 'unitConversions.BASE_UNIT_OR_EACH.volume x units.ST.sizeInBaseUnits',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AM',
        status: 'CONTAINS',
        evidence: {
          fact: 'allergen.AM',
          value: 'CONTAINS',
          display: 'Contains milk',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[allergen]', retrievedAt: null },
        },
      },
      {
        code: 'AW',
        status: 'FREE_FROM',
        evidence: {
          fact: 'allergen.AW',
          value: 'FREE_FROM',
          display: 'Free from cereals containing gluten',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[nonAllergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['FREE_FROM_LACTOSE'],
    facts: [
      {
        fact: 'reliability',
        value: 1.0,
        display: 'Delivered in full 100% historically',
        level: 'ESTIMATE',
        source: { system: 'valio-aimo.delivery-reliability', field: 'deliveryReliability', retrievedAt: null },
      },
      {
        fact: 'co2',
        value: 1.1,
        display: '1.100 kg CO2e/kg · product-group average',
        level: 'ESTIMATE',
        source: { system: 'valio-aimo.carbon-footprint', field: 'AVG kg CO2-eq/kg', retrievedAt: null },
      },
    ],
  },
  {
    productId: 'MOCK-OAT-GF',
    name: 'Synthetic oat drink 1 l UHT gluten-free',
    category: '10400',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '1',
      unit: 'l',
      level: 'DECLARED',
      method: 'unitConversions.BASE_UNIT_OR_EACH.volume x units.ST.sizeInBaseUnits',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AW',
        status: 'FREE_FROM',
        evidence: {
          fact: 'allergen.AW',
          value: 'FREE_FROM',
          display: 'Free from cereals containing gluten',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[nonAllergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['FREE_FROM_GLUTEN', 'VEGAN'],
    substitutions: ['MOCK-OAT-SUB'],
    facts: [
      {
        fact: 'reliability',
        value: 0.99,
        display: 'Delivered in full 99.0% historically',
        level: 'ESTIMATE',
        source: { system: 'valio-aimo.delivery-reliability', field: 'deliveryReliability', retrievedAt: null },
      },
    ],
  },
  {
    productId: 'MOCK-OAT-SUB',
    name: 'Synthetic listed substitute oat drink 1 l (not gluten-free)',
    category: '10400',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '1',
      unit: 'l',
      level: 'ESTIMATE',
      method: 'name',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AW',
        status: 'CONTAINS',
        evidence: {
          fact: 'allergen.AW',
          value: 'CONTAINS',
          display: 'Contains cereals containing gluten',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[allergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['VEGAN'],
    facts: [],
  },
  {
    productId: 'MOCK-BARISTA-CONFLICT',
    name: 'Synthetic barista oat drink 0.5 l (K1 conflict)',
    category: '10400',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '0.5',
      unit: 'l',
      level: 'DECLARED',
      method: 'unitConversions.BASE_UNIT_OR_EACH.volume x units.ST.sizeInBaseUnits',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AW',
        status: 'CONTAINS',
        evidence: {
          fact: 'allergen.AW',
          value: 'CONTAINS',
          display: 'Contains cereals containing gluten',
          level: 'DECLARED',
          conflict: { code: 'K1', message: 'Claims FREE_FROM_GLUTEN while allergen AW is CONTAINS.' },
          source: { system: 'valio-aimo.product-data', field: 'classifications[allergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['FREE_FROM_GLUTEN'],
    facts: [],
  },
  {
    productId: 'MOCK-PASTA-GF',
    name: 'Synthetic gluten-free macaroni 5 kg',
    category: '14100',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '5',
      unit: 'kg',
      level: 'DECLARED',
      method: 'unitConversions.BASE_UNIT_OR_EACH.netWeight x units.ST.sizeInBaseUnits',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AW',
        status: 'FREE_FROM',
        evidence: {
          fact: 'allergen.AW',
          value: 'FREE_FROM',
          display: 'Free from cereals containing gluten',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[nonAllergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['FREE_FROM_GLUTEN'],
    facts: [
      {
        fact: 'reliability',
        value: 0.997,
        display: 'Delivered in full 99.7% historically',
        level: 'ESTIMATE',
        source: { system: 'valio-aimo.delivery-reliability', field: 'deliveryReliability', retrievedAt: null },
      },
    ],
  },
  {
    productId: 'MOCK-PASTA-UNDECLARED',
    name: 'Synthetic organic gluten-free fusilli 250 g (no allergen declaration)',
    category: '14100',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '0.25',
      unit: 'kg',
      level: 'ESTIMATE',
      method: 'name',
    },
    hasAllergenDeclaration: false,
    allergens: [],
    claims: ['FREE_FROM_GLUTEN'],
    facts: [],
  },
  {
    productId: 'MOCK-PASTA-NAME-ONLY',
    name: '12x Synthetic gluten-free macaroni 400g',
    category: '14100',
    isFood: true,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: {
      value: '4.8',
      unit: 'kg',
      level: 'ESTIMATE',
      method: 'name',
    },
    hasAllergenDeclaration: true,
    allergens: [
      {
        code: 'AW',
        status: 'FREE_FROM',
        evidence: {
          fact: 'allergen.AW',
          value: 'FREE_FROM',
          display: 'Free from cereals containing gluten',
          level: 'DECLARED',
          source: { system: 'valio-aimo.product-data', field: 'classifications[nonAllergen]', retrievedAt: null },
        },
      },
    ],
    claims: ['FREE_FROM_GLUTEN'],
    deposits: [{ productId: 'MOCK-DEP-CRATE', quantity: '1', unitPriceExclVat: null }],
    facts: [
      {
        fact: 'reliability',
        value: 0.91,
        display: 'Delivered in full 91.0% historically',
        level: 'ESTIMATE',
        source: { system: 'valio-aimo.delivery-reliability', field: 'deliveryReliability', retrievedAt: null },
      },
    ],
  },
  {
    productId: 'MOCK-CLEANING-UNKNOWN',
    name: 'Synthetic kitchen detergent crate',
    category: '90000',
    isFood: false,
    salesUnit: 'ST',
    baseUnit: 'ST',
    allowedLotSize: '1',
    contentPerSalesUnit: null,
    hasAllergenDeclaration: false,
    allergens: [],
    claims: [],
    facts: [],
  },
]

const SYNTHETIC_PROPOSALS: readonly Proposal[] = [
  scenarioA as unknown as Proposal,
  scenarioB as unknown as Proposal,
  scenarioC as unknown as Proposal,
]

export async function fetchReadinessInsights(): Promise<{
  insights: ReadinessInsights
  source: 'GATEWAY' | 'MOCK'
}> {
  try {
    const res = await fetch('/v0.1/insights', {
      headers: { Accept: 'application/json' },
    })
    if (res.ok) {
      const data = (await res.json()) as ReadinessInsights
      return { insights: data, source: 'GATEWAY' }
    }
  } catch {
    // Dev/test only: fall back to deterministic synthetic computation when the backend is unreachable
  }
  if (!ALLOW_MOCK) throw new Error('Readiness data is unavailable right now.')
  return {
    insights: computeInsightsFromData(SYNTHETIC_CATALOG_PRODUCTS, SYNTHETIC_PROPOSALS, SYNTHETIC_VENDOR_MAP),
    source: 'MOCK',
  }
}

// T83: short labels; the full master-data sentence stays available as a tooltip.
const GAP_SHORT: Record<string, { title: string; fix: string }> = {
  CONTENT_NAME_ONLY_WEAK: { title: 'Pack size from name only', fix: 'Declare pack size (unitConversions)' },
  MISSING_ALLERGEN_DECLARATION: { title: 'No allergen declaration', fix: 'Declare allergens (or X99)' },
  CONTENT_UNKNOWN: { title: 'Pack size unknown', fix: 'Declare net weight or volume' },
  IMPLICIT_ONLY_ALLERGENS: { title: 'Free-from only by omission', fix: 'Declare FREE_FROM explicitly' },
  LOW_RELIABILITY: { title: 'Reliability below 95 %', fix: 'Improve fulfilment or buffer stock' },
  UNPRICED_DEPOSIT: { title: 'Unpriced deposit', fix: 'Price every deposit material' },
  UNSAFE_VALIO_SUBSTITUTE: { title: 'Unsafe listed substitute', fix: 'Fix the substitute links' },
  DATA_CONFLICT: { title: 'Contradictory declarations', fix: 'Validate imports (K1/K2/K5/K6)' },
}

const FIX_BY_REASON: Partial<Record<ReasonCode, string>> = {
  ALLERGEN_UNKNOWN: 'Declare allergens (or X99)',
  ALLERGEN_IMPLICIT: 'Declare FREE_FROM explicitly',
  QUANTITY_BASIS_WEAK: 'Declare pack size (unitConversions)',
  QUANTITY_BASIS_UNKNOWN: 'Declare net weight or volume',
  DATA_CONFLICT: 'Reconcile the declarations',
  DEPOSIT_COST_UNVERIFIED: 'Price every deposit material',
  SUBSTITUTE_REJECTED: 'Replace the unsafe substitute',
  LOW_RELIABILITY: 'Improve delivery reliability',
}

function EffectBadge({ effect, approvable }: { effect: string; approvable?: boolean }) {
  const isAsk = effect === 'ASK'
  const cls = isAsk ? 'text-[var(--ask)]' : 'text-[var(--block)]'
  const note =
    approvable === undefined ? undefined : approvable ? 'Approvable by a human (P2.2)' : 'Never approvable (P2.3)'
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-xs font-bold ${cls}`}
      title={note}
    >
      {isAsk ? <AlertTriangle size={12} aria-hidden /> : <ShieldAlert size={12} aria-hidden />}
      <span>{effect}</span>
    </span>
  )
}

function ReadinessLoading() {
  return (
    <div className="grid grid-cols-1 gap-3" aria-label="Loading readiness insights" aria-busy="true">
      <div className="readiness-toolbar">
        <Skeleton className="skeleton-heading" />
      </div>
      <section className="panel valio-catalogue-panel">
        <h1 className="text-lg font-bold text-[var(--ink)]">
          Agent readiness: data quality = autonomous sales
        </h1>
        <p className="muted small mt-1">
          <strong>AUTO</strong> needs verified or declared facts without conflict.
        </p>
        <div className="mt-4 grid gap-4 border-t border-[var(--line)] pt-4 sm:grid-cols-2 lg:grid-cols-4 lg:divide-x lg:divide-[var(--line)]">
          {[0, 1, 2, 3].map((i) => (
            <LoadingSection
              key={i}
              label="catalogue statistics"
              variant="stats"
              className={i > 0 ? 'lg:pl-4' : ''}
            />
          ))}
        </div>
      </section>
      <p className="muted text-sm">Statistics cover your session only.</p>
      <LoadingSection
        label="decision statistics"
        variant="stats"
        className="panel valio-decisions-panel"
      />
      <LoadingSection label="catalogue data gaps" variant="table" className="panel" />
      <LoadingSection label="supplier readiness" variant="table" className="panel" />
    </div>
  )
}

/** Valio Aimo readiness view (§14, D6, T27): data quality = autonomous sales. */
export function ValioView() {
  const [supplierQuery, setSupplierQuery] = useState('')
  const [foodOnly, setFoodOnly] = useState(false)
  const [reasonFilter, setReasonFilter] = useState<string>('ALL')
  const [showAllSuppliers, setShowAllSuppliers] = useState(false)
  const [fixScopeFilter, setFixScopeFilter] = useState<'ALL' | 'SUPPLIER' | 'PRODUCT'>('ALL')
  const [useReferenceScenariosWhenEmpty, setUseReferenceScenariosWhenEmpty] = useState(false)

  const health = useQuery({
    queryKey: ['gateway-health'],
    queryFn: () => gateway.checkHealth(),
    retry: false,
  })

  const query = useQuery({
    queryKey: ['valio-readiness-insights'],
    queryFn: fetchReadinessInsights,
    retry: ALLOW_MOCK ? false : 3,
  })

  const data = useMemo(
    () =>
      query.data ?? {
        insights: computeInsightsFromData(
          SYNTHETIC_CATALOG_PRODUCTS,
          SYNTHETIC_PROPOSALS,
          SYNTHETIC_VENDOR_MAP,
        ),
        source: 'MOCK' as const,
      },
    [query.data],
  )

  const { insights, source } = data
  const { catalogSummary, gapBreakdown, suppliers, fixList, decisionStats } = insights

  const referenceDecisionStats = useMemo(
    () => computeDecisionStatisticsFromProposals(SYNTHETIC_PROPOSALS),
    [],
  )
  const displayedDecisionStats =
    decisionStats.totalProposals === 0 && useReferenceScenariosWhenEmpty
      ? referenceDecisionStats
      : decisionStats

  const filteredSuppliers = useMemo(() => {
    const q = supplierQuery.trim().toLowerCase()
    return suppliers.filter((s) => {
      if (foodOnly && s.foodProducts <= 0) return false
      if (q && !s.supplierName.toLowerCase().includes(q)) return false
      if (reasonFilter !== 'ALL' && !s.blockingReasonCodes.includes(reasonFilter as ReasonCode)) {
        return false
      }
      return true
    })
  }, [suppliers, supplierQuery, foodOnly, reasonFilter])

  const filteredSupplierTotals = useMemo(() => {
    let total = 0
    let food = 0
    let ready = 0
    let blocked = 0
    for (const s of filteredSuppliers) {
      total += s.totalProducts
      food += s.foodProducts
      ready += s.autoReadyProducts
      blocked += s.blockedFromAutoProducts
    }
    return { total, food, ready, blocked }
  }, [filteredSuppliers])

  const visibleSuppliers = showAllSuppliers ? filteredSuppliers : filteredSuppliers.slice(0, 25)

  const filteredFixes = useMemo(
    () => fixList.filter((f) => (fixScopeFilter === 'ALL' ? true : f.scope === fixScopeFilter)),
    [fixList, fixScopeFilter],
  )

  if (query.isPending) return <ReadinessLoading />
  if (!query.data || (query.isError && !ALLOW_MOCK)) {
    return (
      <p className="notice notice-error" role="alert">
        Readiness data is unavailable right now.{' '}
        <button type="button" className="btn btn-scenario text-xs" onClick={() => query.refetch()}>
          Retry
        </button>
      </p>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <a
          href="/"
          aria-label="Back to the purchasing flow"
          className="inline-flex items-center gap-1 text-sm font-semibold text-[var(--green)] underline"
        >
          <ArrowLeft size={15} aria-hidden /> Buy
        </a>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => query.refetch()}
            className="btn btn-scenario log-scenario-pill text-xs font-semibold"
          >
            <RefreshCw size={13} aria-hidden />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Hero & Catalogue Summary */}
      <section className="panel valio-catalogue-panel" aria-labelledby="valio-readiness-title">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 id="valio-readiness-title" className="text-lg font-bold text-[var(--ink)]">
              Agent readiness: data quality = autonomous sales
            </h1>
            <p className="muted small mt-1">
              <strong>AUTO</strong> needs verified or declared facts without conflict.
            </p>
          </div>
        </div>

        <div
          className="mt-4 grid gap-4 border-t border-[var(--line)] pt-4 sm:grid-cols-2 lg:grid-cols-4 lg:divide-x lg:divide-[var(--line)]"
          aria-label="Catalogue readiness summary"
        >
          <div className="min-w-0">
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
              Catalogue
            </span>
            <p className="mt-1 text-2xl font-bold text-[var(--ink)]">
              {catalogSummary.totalProducts.toLocaleString('en-US')}{' '}
              <span className="text-sm font-normal text-[var(--muted)]">products</span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              {catalogSummary.totalSuppliers.toLocaleString('en-US')} suppliers ·{' '}
              {catalogSummary.foodProducts.toLocaleString('en-US')} food
            </p>
          </div>

          <div className="min-w-0 lg:pl-4">
            <span className="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
              <CheckCircle2 size={14} aria-hidden className="text-[var(--auto)]" /> AUTO-ready
            </span>
            <p className="mt-1 text-2xl font-bold text-[var(--auto)]">
              {catalogSummary.autoReadyProducts.toLocaleString('en-US')}{' '}
              <span className="text-sm font-semibold">({formatPercent(catalogSummary.autoReadyRate)})</span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Food: <strong className="text-[var(--ink)]">{catalogSummary.foodAutoReadyProducts.toLocaleString('en-US')}</strong> /{' '}
              {catalogSummary.foodProducts.toLocaleString('en-US')}
            </p>
          </div>

          <div className="min-w-0 lg:pl-4">
            <span className="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
              <ShieldAlert size={14} aria-hidden className="text-[var(--block)]" /> Blocked from AUTO
            </span>
            <p className="mt-1 text-2xl font-bold text-[var(--block)]">
              {catalogSummary.blockedFromAutoProducts.toLocaleString('en-US')}{' '}
              <span className="text-sm font-semibold">
                ({formatPercent(1 - Number(catalogSummary.autoReadyRate))})
              </span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              {catalogSummary.missingAllergensFood.toLocaleString('en-US')} no allergens ·{' '}
              {catalogSummary.contentWeakNameEstimate.toLocaleString('en-US')} name-only size ·{' '}
              {catalogSummary.contentUnknown.toLocaleString('en-US')} unknown size
            </p>
          </div>

          <div className="min-w-0 lg:pl-4">
            <span className="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
              <Database size={14} aria-hidden className="text-[var(--ask)]" /> Data traps
            </span>
            <p className="mt-1 text-2xl font-bold text-[var(--ask)]">
              {(catalogSummary.dataConflicts + catalogSummary.unsafeSubstitutes).toLocaleString('en-US')}{' '}
              <span className="text-sm font-semibold">traps</span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              <span
                title={
                  Object.entries(catalogSummary.conflictsByCode)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(', ') || 'No conflicts'
                }
              >
                <strong className="text-[var(--ink)]">{catalogSummary.dataConflicts}</strong> conflicts
              </span>{' '}
              · <strong className="text-[var(--ink)]">{catalogSummary.unsafeSubstitutes}</strong> unsafe substitutes ·{' '}
              <strong className="text-[var(--ink)]">{catalogSummary.x99Products}</strong> X99
            </p>
          </div>
        </div>
      </section>

      <p className="muted text-sm">
        Statistics cover your session only.
      </p>
      {/* Live AUTO / ASK / BLOCK Statistics */}
      <section className="panel valio-decisions-panel" aria-labelledby="live-decision-stats-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="live-decision-stats-title" className="panel-title mb-0 inline-flex items-center gap-2">
              <BarChart3 size={15} aria-hidden /> Decisions
            </h2>
            <p className="muted small mt-0.5">
              <strong>{displayedDecisionStats.totalProposals}</strong> proposals ·{' '}
              <strong>{displayedDecisionStats.activeProposals}</strong> active ·{' '}
              <strong>{displayedDecisionStats.supersededProposals}</strong> superseded ·{' '}
              <strong>{displayedDecisionStats.confirmedSimulated}</strong> confirmed
            </p>
          </div>
          {ALLOW_MOCK && decisionStats.totalProposals === 0 && (
            <button
              type="button"
              onClick={() => setUseReferenceScenariosWhenEmpty((prev) => !prev)}
              className="btn btn-scenario text-xs font-semibold"
            >
              <span>
                {useReferenceScenariosWhenEmpty
                  ? 'Show empty'
                  : 'Preview A/B/C'}
              </span>
            </button>
          )}
        </div>

        <div
          className="mt-4 grid gap-4 border-t border-[var(--line)] pt-4 sm:grid-cols-3 sm:divide-x sm:divide-[var(--line)]"
          aria-label="AUTO ASK BLOCK breakdown"
        >
          <div className="min-w-0">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
                AUTO · agent
              </span>
              <span className="text-xs font-bold text-[var(--auto)]">
                {formatPercent(displayedDecisionStats.autoRate)}
              </span>
            </div>
            <p className="mt-1 text-2xl font-bold text-[var(--auto)]">
              {displayedDecisionStats.byDecision.AUTO}{' '}
              <span className="text-xs font-normal text-[var(--muted)]">
                ({displayedDecisionStats.byDecisionActive.AUTO} active)
              </span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              <strong className="text-[var(--ink)]">{formatMoney(displayedDecisionStats.autoValueExclVat)}</strong> excl. VAT
            </p>
          </div>

          <div className="min-w-0 sm:pl-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
                ASK · human
              </span>
              <span className="text-xs font-bold text-[var(--ask)]">
                {formatPercent(displayedDecisionStats.askRate)}
              </span>
            </div>
            <p className="mt-1 text-2xl font-bold text-[var(--ask)]">
              {displayedDecisionStats.byDecision.ASK}{' '}
              <span className="text-xs font-normal text-[var(--muted)]">
                ({displayedDecisionStats.pendingApproval} pending)
              </span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              <strong className="text-[var(--ink)]">{formatMoney(displayedDecisionStats.askValueExclVat)}</strong> excl. VAT
            </p>
          </div>

          <div className="min-w-0 sm:pl-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
                BLOCK · mandate
              </span>
              <span className="text-xs font-bold text-[var(--block)]">
                {formatPercent(displayedDecisionStats.blockRate)}
              </span>
            </div>
            <p className="mt-1 text-2xl font-bold text-[var(--block)]">
              {displayedDecisionStats.byDecision.BLOCK}{' '}
              <span className="text-xs font-normal text-[var(--muted)]">
                ({displayedDecisionStats.byDecisionActive.BLOCK} active)
              </span>
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              <strong className="text-[var(--ink)]">{formatMoney(displayedDecisionStats.blockValueExclVat)}</strong> excl. VAT
            </p>
          </div>
        </div>

        {(displayedDecisionStats.reasonCounts.length > 0 ||
          displayedDecisionStats.excludedReasonCounts.length > 0) && (
          <div className="mt-4 grid gap-6 border-t border-[var(--line)] pt-4 md:grid-cols-2">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
                Order reasons
              </h3>
              <ul className="mt-2 grid gap-1.5 text-xs">
                {displayedDecisionStats.reasonCounts.map((r) => (
                  <li key={r.code} className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--line)] py-1 last:border-b-0">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="chip">{reasonChip(r.code)}</span>
                      <code className="text-[11px] text-[var(--muted)]">{r.code}</code>
                    </span>
                    <span className="font-semibold">
                      {r.occurrences}× ({r.effect})
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
                Exclusions ({displayedDecisionStats.totalExcludedCandidates})
              </h3>
              <ul className="mt-2 grid gap-1.5 text-xs">
                {displayedDecisionStats.excludedReasonCounts.map((r) => (
                  <li key={r.code} className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--line)] py-1 last:border-b-0">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="chip">{reasonChip(r.code)}</span>
                      <code className="text-[11px] text-[var(--muted)]">{r.code}</code>
                    </span>
                    <span className="font-semibold">
                      {r.occurrences}× ({r.effect})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </section>

      {/* Catalogue Data Gaps that Block AUTO */}
      <section className="panel" aria-labelledby="gap-breakdown-title">
        <h2 id="gap-breakdown-title" className="panel-title">
          Data gaps
        </h2>
        <p className="muted small">
          <strong>ASK</strong> goes to a human; <strong>EXCLUDE</strong> removes the product. Both stop AUTO.
        </p>

        <div className="table-scroll mt-3">
          <table className="data-table text-xs">
            <caption className="visually-hidden">Catalogue data gaps blocking autonomous sales</caption>
            <thead>
              <tr>
                <th scope="col">Data gap</th>
                <th scope="col">Reason</th>
                <th scope="col">Effect</th>
                <th scope="col" className="num">
                  Products
                </th>
                <th scope="col" className="num">
                  Suppliers
                </th>
                <th scope="col">Fix</th>
              </tr>
            </thead>
            <tbody>
              {gapBreakdown.map((gap) => (
                <tr key={gap.gapId}>
                  <th scope="row" className="font-semibold" title={gap.title}>
                    {GAP_SHORT[gap.gapId]?.title ?? gap.title}
                  </th>
                  <td>
                    <div className="flex flex-wrap items-center gap-1">
                      <span className="chip">{reasonChip(gap.reasonCode)}</span>
                      <code className="text-[11px] text-[var(--muted)]" title={gap.rule}>
                        {gap.reasonCode}
                      </code>
                    </div>
                  </td>
                  <td>
                    <EffectBadge effect={gap.effect} approvable={gap.approvableByHuman} />
                  </td>
                  <td className="num font-semibold">
                    {gap.affectedProducts.toLocaleString('en-US')}{' '}
                    <span className="font-normal text-[var(--muted)]">
                      ({formatPercent(gap.shareOfCatalog)})
                    </span>
                  </td>
                  <td className="num">{gap.affectedSuppliers.toLocaleString('en-US')}</td>
                  <td title={gap.fixAction}>{GAP_SHORT[gap.gapId]?.fix ?? gap.fixAction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Data Gaps by Supplier */}
      <section className="panel" aria-labelledby="supplier-gaps-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="supplier-gaps-title" className="panel-title mb-0 inline-flex items-center gap-2">
              <Filter size={15} aria-hidden /> Suppliers
            </h2>
            <p className="muted small mt-0.5">
              <strong>{visibleSuppliers.length}</strong> of{' '}
              <strong>{filteredSuppliers.length}</strong> suppliers ·{' '}
              <strong>{filteredSupplierTotals.total.toLocaleString('en-US')}</strong> products (
              {filteredSupplierTotals.ready.toLocaleString('en-US')} AUTO-ready ·{' '}
              {filteredSupplierTotals.blocked.toLocaleString('en-US')} blocked)
            </p>
          </div>
          <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
            <input
              type="search"
              value={supplierQuery}
              onChange={(e) => setSupplierQuery(e.target.value)}
              placeholder="Search suppliers…"
              aria-label="Filter suppliers by name"
              className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs"
            />
            <button
              type="button"
              onClick={() => setFoodOnly((prev) => !prev)}
              aria-pressed={foodOnly}
              className={`btn text-xs font-semibold ${foodOnly ? 'btn-primary' : 'btn-scenario'}`}
            >
              <span>{foodOnly ? 'Food only ✓' : 'Food only'}</span>
            </button>
            <select
              value={reasonFilter}
              onChange={(e) => setReasonFilter(e.target.value)}
              aria-label="Filter suppliers by blocking reason"
              className="min-w-0 max-w-full rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs font-semibold"
            >
              <option value="ALL">All reasons</option>
              <option value="ALLERGEN_UNKNOWN">No allergens</option>
              <option value="QUANTITY_BASIS_WEAK">Name-only size</option>
              <option value="QUANTITY_BASIS_UNKNOWN">Unknown size</option>
              <option value="DATA_CONFLICT">Data conflict</option>
              <option value="DEPOSIT_COST_UNVERIFIED">Unpriced deposit</option>
              <option value="SUBSTITUTE_REJECTED">Unsafe substitute</option>
              <option value="LOW_RELIABILITY">Low reliability</option>
            </select>
          </div>
        </div>

        <div className="table-scroll mt-3">
          <table className="data-table text-xs">
            <caption className="visually-hidden">Supplier readiness and data gaps</caption>
            <thead>
              <tr>
                <th scope="col">Supplier</th>
                <th scope="col" className="num">
                  Products
                </th>
                <th scope="col" className="num">
                  AUTO-ready
                </th>
                <th scope="col" className="num">
                  Blocked
                </th>
                <th scope="col" className="num">
                  No allergens
                </th>
                <th scope="col" className="num">
                  Name-only size
                </th>
                <th scope="col" className="num">
                  Unknown size
                </th>
                <th scope="col" className="num">
                  Conf/Dep/Sub
                </th>
                <th scope="col">Blockers</th>
                <th scope="col">Top fix</th>
              </tr>
            </thead>
            <tbody>
              {visibleSuppliers.map((sup) => (
                <tr key={sup.supplierName}>
                  <th scope="row" className="font-semibold">
                    {sup.supplierName}
                  </th>
                  <td className="num">
                    {sup.totalProducts.toLocaleString('en-US')}{' '}
                    <span className="text-[var(--muted)]">({sup.foodProducts.toLocaleString('en-US')})</span>
                  </td>
                  <td className="num font-semibold text-[var(--auto)]">
                    {sup.autoReadyProducts.toLocaleString('en-US')}{' '}
                    <span className="font-normal text-[var(--muted)]">
                      ({formatPercent(sup.autoReadyRate)})
                    </span>
                  </td>
                  <td className="num font-bold text-[var(--block)]">
                    {sup.blockedFromAutoProducts.toLocaleString('en-US')}
                  </td>
                  <td className="num">{sup.missingAllergensFood.toLocaleString('en-US')}</td>
                  <td className="num">{sup.contentWeakNameEstimate.toLocaleString('en-US')}</td>
                  <td className="num">{sup.contentUnknown.toLocaleString('en-US')}</td>
                  <td className="num">
                    {sup.dataConflicts} / {sup.unpricedDeposits} / {sup.unsafeSubstitutes}
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {sup.blockingReasonCodes.length > 0 ? (
                        sup.blockingReasonCodes.map((code) => (
                          <span key={code} className="chip">
                            {code}
                          </span>
                        ))
                      ) : (
                        <span className="text-[var(--auto)] font-semibold">AUTO-ready</span>
                      )}
                    </div>
                  </td>
                  <td title={sup.topFix}>
                    {sup.blockingReasonCodes.length > 0
                      ? (FIX_BY_REASON[sup.blockingReasonCodes[0]] ?? sup.topFix)
                      : 'None needed'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {filteredSuppliers.length > 25 && (
          <div className="mt-3 flex justify-center">
            <button
              type="button"
              onClick={() => setShowAllSuppliers((prev) => !prev)}
              className="btn btn-scenario text-xs font-semibold"
            >
              <span>
                {showAllSuppliers
                  ? 'Show top 25'
                  : `Show all (${filteredSuppliers.length.toLocaleString('en-US')})`}
              </span>
            </button>
          </div>
        )}
      </section>

      {/* Actionable Fix List */}
      <section className="panel" aria-labelledby="fix-list-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="fix-list-title" className="panel-title mb-0 inline-flex items-center gap-2">
              <Wrench size={15} aria-hidden /> Fix list
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {(['ALL', 'SUPPLIER', 'PRODUCT'] as const).map((scope) => (
              <button
                key={scope}
                type="button"
                onClick={() => setFixScopeFilter(scope)}
                className={`btn text-xs font-semibold ${fixScopeFilter === scope ? 'btn-primary' : 'btn-scenario'}`}
              >
                <span>
                  {scope === 'ALL'
                    ? `All fixes (${fixList.length})`
                    : scope === 'SUPPLIER'
                      ? `Suppliers (${fixList.filter((f) => f.scope === 'SUPPLIER').length})`
                      : `Products (${fixList.filter((f) => f.scope === 'PRODUCT').length})`}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="table-scroll mt-3">
          <table className="data-table text-xs">
            <caption className="visually-hidden">Prioritised master-data fix list</caption>
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">Product</th>
                <th scope="col">Reason</th>
                <th scope="col">Fix</th>
                <th scope="col" className="num">
                  Unlocks
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredFixes.map((fix) => (
                <tr key={fix.fixId} title={fix.problem}>
                  <th scope="row" className="font-mono text-[11px]">
                    <span className="font-bold">{fix.fixId}</span>
                    <span className="block text-[var(--muted)]">
                      {fix.priority} · {fix.scope}
                    </span>
                  </th>
                  <td>
                    <strong className="block">{fix.supplierName}</strong>
                    {fix.productId && (
                      <span className="text-[var(--muted)]">
                        <code>{fix.productId}</code> · {fix.productName}
                      </span>
                    )}
                  </td>
                  <td>
                    <div className="grid gap-1">
                      <span className="inline-flex flex-wrap items-center gap-1">
                        <span className="chip">{reasonChip(fix.reasonCode)}</span>
                        <code className="text-[11px] text-[var(--muted)]">{fix.reasonCode}</code>
                      </span>
                      <EffectBadge effect={fix.effect} />
                    </div>
                  </td>
                  <td title={fix.fixAction}>{FIX_BY_REASON[fix.reasonCode] ?? fix.fixAction}</td>
                  <td className="num font-bold text-[var(--auto)]">
                    +{fix.productsUnlocked.toLocaleString('en-US')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {source === 'GATEWAY' && <ValioCustomerAudit />}
      {source === 'GATEWAY' && <ValioAgentActivity />}
    </div>
  )
}

type SessionChainInfo = {
  sessionId: string
  verified: boolean
  chainLength: number
  headHash: string
  reason?: string | null
}

type CrossSessionVerification = {
  verified: boolean
  chainLength: number
  headHash: string
  reason?: string | null
  sessions?: SessionChainInfo[] | null
}

const VALIO_BUYERS = [
  { id: 'nordic-harbour-hotels', label: 'Nordic Harbour Hotels' },
  { id: 'northbay-school-kitchen', label: 'Northbay School Kitchen' },
] as const

function valioAuditHeaders(customerId: string): Record<string, string> {
  const admin = adminHeaders()
  return Object.keys(admin).length > 0 ? admin : agentHeaders(customerId)
}

async function fetchValioCustomerAudit(customerId: string): Promise<{
  trail: AuditTrail
  verification: CrossSessionVerification
}> {
  const headers = { ...valioAuditHeaders(customerId), Accept: 'application/json' }
  const params = `customerId=${encodeURIComponent(customerId)}&scope=all`
  const [trailRes, verRes] = await Promise.all([
    fetch(`/v0.1/audit?${params}`, { headers, signal: AbortSignal.timeout(15000) }),
    fetch(`/v0.1/audit/verify?${params}`, { headers, signal: AbortSignal.timeout(15000) }),
  ])
  const trailBody = await trailRes.json()
  if (!trailRes.ok) {
    throw new Error(trailBody.error?.message ?? 'Could not load customer audit trail.')
  }
  const verBody = await verRes.json()
  if (!verRes.ok) {
    throw new Error(verBody.error?.message ?? 'Could not verify customer audit chain.')
  }
  return { trail: trailBody as AuditTrail, verification: verBody as CrossSessionVerification }
}

function ValioCustomerAudit() {
  const [customerId, setCustomerId] = useState<string>(VALIO_BUYERS[0].id)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const audit = useQuery({
    queryKey: ['valio-customer-audit', customerId],
    queryFn: () => fetchValioCustomerAudit(customerId),
    refetchInterval: 5000,
    retry: false,
  })

  async function downloadExport(format: 'json' | 'csv') {
    setDownloadError(null)
    try {
      const params = `customerId=${encodeURIComponent(customerId)}&scope=all&format=${format}`
      const res = await fetch(`/v0.1/audit/export?${params}`, {
        headers: valioAuditHeaders(customerId),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error?.message ?? 'Could not export customer audit trail.')
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `decision-log-customer-${customerId}-all-sessions.${format}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed.')
    }
  }

  const entries = audit.data?.trail.entries ?? []
  const verification = audit.data?.verification
  const sessions = verification?.sessions ?? []

  return (
    <section className="panel min-w-0" aria-labelledby="valio-customer-audit-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="valio-customer-audit-title" className="panel-title mb-0">
            Buyer audit across sessions
          </h2>
          <p className="muted small mt-0.5">
            Read-only Valio view (<code>scope=all</code>, public demo credentials); each session’s SHA-256 chain is verified separately.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            aria-label="Select buyer for cross-session audit"
            className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs font-semibold"
          >
            {VALIO_BUYERS.map((buyer) => (
              <option key={buyer.id} value={buyer.id}>
                {buyer.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => downloadExport('json')}
            className="log-download-btn"
          >
            <Download size={12} aria-hidden />
            <span>JSON</span>
          </button>
          <button
            type="button"
            onClick={() => downloadExport('csv')}
            className="log-download-btn"
          >
            <Download size={12} aria-hidden />
            <span>CSV</span>
          </button>
        </div>
      </div>

      {(audit.error || downloadError) && (
        <p className="notice notice-error" role="alert">
          {downloadError ?? (audit.error as Error)?.message}
        </p>
      )}

      {verification && (
        <div
          className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--line)] pt-2.5 text-xs"
          role="status"
          aria-label="Cross-session hash-chain verification"
        >
          <span className={verification.verified ? 'font-bold text-[var(--auto)]' : 'font-bold text-[var(--block)]'}>
            {verification.verified
              ? `All session chains verified ✓ (${sessions.length} session${sessions.length === 1 ? '' : 's'} · ${verification.chainLength} entries)`
              : `Hash-chain mismatch: ${verification.reason ?? 'tampered entry detected'}`}
          </span>
          {sessions.length > 0 && (
            <span className="muted font-mono text-[11px]">
              {sessions
                .map((s) => `${s.sessionId}: ${s.chainLength} ${s.verified ? '✓' : '✗'}`)
                .join(' · ')}
            </span>
          )}
        </div>
      )}

      {entries.length === 0 ? (
        <p className="muted small mt-3 border-t border-[var(--line)] pt-2.5">
          No decision-log entries recorded for this buyer across active sessions yet.
        </p>
      ) : (
        <div className="table-scroll mt-3 border-t border-[var(--line)]">
          <table className="data-table text-xs">
            <caption className="visually-hidden">Cross-session decision log for selected buyer</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Proposal</th>
                <th scope="col">Actor</th>
                <th scope="col">Event</th>
                <th scope="col">Summary</th>
              </tr>
            </thead>
            <tbody>
              {[...entries].reverse().slice(0, 25).map((entry) => (
                <tr key={`${entry.entryId}-${entry.timestamp}`}>
                  <td className="whitespace-nowrap font-mono text-[11px] text-[var(--muted)]">
                    {formatTime(entry.timestamp)}
                  </td>
                  <td className="whitespace-nowrap font-mono text-[11px]">
                    {entry.proposalId ? <code>{entry.proposalId}</code> : '—'}
                  </td>
                  <td className="whitespace-nowrap">{entry.actor}</td>
                  <td className="whitespace-nowrap font-semibold">{entry.event}</td>
                  <td>{entry.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export default ValioView
