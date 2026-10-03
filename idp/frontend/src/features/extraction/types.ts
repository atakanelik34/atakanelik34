import type { BBox } from '@/features/viewer/api'

export interface Provenance {
  method?: string
  provider?: string
  provider_version?: string
  page?: number | null
  bbox?: BBox | null
  source_text?: string | null
  extracted_at?: string
  pipeline_version?: string
  schema_version_id?: string
  normalization_error?: string | null
}

export interface FieldValue {
  id: string
  path: string
  row_id: string
  value: unknown
  original_value: unknown
  raw_text: string | null
  confidence: number
  threshold: number
  below_threshold: boolean
  required: boolean
  type: string | null
  status: 'extracted' | 'missing' | 'accepted' | 'corrected' | 'rejected'
  normalized: boolean
  method: string | null
  provider: string | null
  provenance: Provenance
  alternatives: { value: unknown; confidence: number; method: string; page: number | null; bbox: BBox | null }[]
}

export interface ValidationOutcome {
  rule: string
  outcome: 'PASS' | 'WARNING' | 'FAIL' | 'REQUIRES_HUMAN'
  fields: string[]
  message: string
}

export interface PartResult {
  part_id: string
  pages: [number, number]
  classification: {
    document_type: string | null
    document_type_name: string | null
    confidence: number
    classifier: string
    reasons: string[]
  }
  schema_version: number | null
  extraction: { route: string; providers: string[]; route_trace: RouteTrace | Record<string, never>; metrics: Record<string, unknown> } | null
  fields: Record<string, FieldValue>
  tables: Record<string, { row_id: string; cells: Record<string, FieldValue> }[]>
  validation: ValidationOutcome[]
  enrichment: Record<string, unknown>[]
  actions: Record<string, unknown>[]
}

export interface ProviderAttempt {
  provider: string
  status: 'ok' | 'skipped' | 'failed' | 'timeout' | 'circuit_open'
  version?: string
  candidates?: number
  duration_ms?: number
  estimated_cost?: number
  error?: string
  reasons?: string[]
}

export interface RouteTrace {
  route: string
  routing_version: number
  policy_version: number
  signals: Record<string, string | number | boolean | null>
  reasons: string[]
  planned_stages: string[][]
  rejected: { provider: string; reason: string }[]
  stages: { stage: number; attempts: ProviderAttempt[]; unresolved: string[]; outcome: 'accepted' | 'escalated' | 'exhausted' }[]
  estimated_cost: number
}

export interface ProcessingResult {
  document_id: string
  status: string
  job_id: string | null
  parts: PartResult[]
  pages: { number: number; text_source: string | null; text_quality: number | null; ocr_status: string | null }[]
  metrics: { steps: Record<string, number>; duration_ms: number; estimated_cost: number }
  errors: { category: string; code: string; message: string }[]
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}
