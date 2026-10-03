export type DocumentStatus =
  | 'RECEIVED'
  | 'QUEUED'
  | 'PROCESSING'
  | 'WAITING_FOR_HUMAN'
  | 'READY_FOR_ACTION'
  | 'COMPLETED'
  | 'FAILED'
  | 'REJECTED'

export const DOCUMENT_STATUSES: DocumentStatus[] = [
  'RECEIVED',
  'QUEUED',
  'PROCESSING',
  'WAITING_FOR_HUMAN',
  'READY_FOR_ACTION',
  'COMPLETED',
  'FAILED',
  'REJECTED',
]

/** Mirrors REPROCESSABLE in backend/src/idp/domain/lifecycle.py; the API enforces it. */
export const REPROCESSABLE: DocumentStatus[] = ['COMPLETED', 'FAILED', 'REJECTED', 'WAITING_FOR_HUMAN']

export const ACTIVE_STATUSES: DocumentStatus[] = ['RECEIVED', 'QUEUED', 'PROCESSING']

export type JobStatus = 'QUEUED' | 'RUNNING' | 'RETRY_SCHEDULED' | 'SUCCEEDED' | 'FAILED' | 'DEAD_LETTERED'
export type StepStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface DocumentSummary {
  id: string
  original_filename: string
  detected_mime_type: string
  size_bytes: number
  status: DocumentStatus
  page_count: number | null
  received_at: string
}

export interface Page {
  page_number: number
  width: number
  height: number
  unit: string
  rotation: number
  has_text_layer: boolean
  char_count: number
  text_source: 'native' | 'ocr' | 'none' | null
  ocr_status: 'not_needed' | 'done' | 'not_configured' | 'failed' | null
  text_quality: number | null
  ocr_confidence: number | null
  language: string | null
  table_density: number | null
  word_count: number | null
  image_width: number | null
  image_height: number | null
}

export interface DocumentDetail extends DocumentSummary {
  declared_mime_type: string | null
  sha256: string
  source: string
  scan_status: string
  uploaded_by_id: string | null
  pages: Page[]
}

export interface DocumentList {
  items: DocumentSummary[]
  next_cursor: string | null
}

export interface Step {
  id: string
  step_key: string
  attempt: number
  status: StepStatus
  provider: string | null
  provider_version: string | null
  metrics: Record<string, unknown>
  error_category: string | null
  error_code: string | null
  error_message: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
}

export interface Job {
  id: string
  workflow_key: string
  workflow_version: number
  pipeline_version: string
  trigger: string
  status: JobStatus
  attempts: number
  max_attempts: number
  next_attempt_at: string | null
  current_step: string | null
  last_error_category: string | null
  last_error_code: string | null
  last_error_message: string | null
  correlation_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobWithSteps extends Job {
  steps: Step[]
}

export interface StatusChange {
  from_status: DocumentStatus
  to_status: DocumentStatus
  reason: string | null
  actor_type: string
  actor_id: string | null
  occurred_at: string
}

export interface Timeline {
  jobs: JobWithSteps[]
  status_changes: StatusChange[]
}

export interface UploadResponse {
  document: DocumentSummary
  job_id: string
}

export interface DocumentPart {
  id: string
  job_id: string
  part_index: number
  page_start: number
  page_end: number
  document_type_id: string | null
  document_type_key: string | null
  document_type_name: string | null
  schema_version_id: string | null
  schema_version: number | null
  classification_confidence: number
  classifier: string
  classification_reasons: string[]
  status: 'classified' | 'unclassified'
}
