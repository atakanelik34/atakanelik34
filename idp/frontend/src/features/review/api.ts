import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { FieldValue, ValidationOutcome } from '@/features/extraction/types'
import { apiRequest } from '@/lib/api'

export interface ReviewReason {
  part_id: string
  rule_id: string
  rule_type: string
  outcome: string
  fields: string[]
  message: string
}

export interface ReviewTask {
  id: string
  document_id: string
  document_name: string
  job_id: string
  status: 'open' | 'in_progress' | 'approved' | 'rejected' | 'sent_back'
  reasons: ReviewReason[]
  assignee_id: string | null
  created_at: string
  resolved_at: string | null
  resolution_note: string | null
}

export interface ReviewPart {
  part_id: string
  pages: [number, number]
  document_type: string | null
  document_type_name: string | null
  classification_confidence: number
  schema_version: number | null
  fields: Record<string, FieldValue>
  tables: Record<string, { row_id: string; cells: Record<string, FieldValue> }[]>
  validation: (ValidationOutcome & {
    rule_type: string
    details: Record<string, unknown>
  })[]
}

export interface ReviewAction {
  id: string
  action: string
  actor_id: string | null
  path: string | null
  row_id: string | null
  original_value: unknown
  corrected_value: unknown
  reason: string | null
  created_at: string
}

export interface ReviewDetail {
  task: ReviewTask
  document: {
    id: string
    original_filename: string
    page_count: number | null
    status: string
  }
  parts: ReviewPart[]
  actions: ReviewAction[]
}

const key = ['reviews'] as const

export function useReviewQueue(status: string | null) {
  return useQuery({
    queryKey: [...key, 'list', status],
    queryFn: ({ signal }) =>
      apiRequest<ReviewTask[]>(`/reviews${status ? `?status=${status}` : ''}`, {
        signal,
      }),
    refetchInterval: 10_000,
  })
}

export function useReview(id: string) {
  return useQuery({
    queryKey: [...key, id],
    queryFn: ({ signal }) => apiRequest<ReviewDetail>(`/reviews/${id}`, { signal }),
  })
}

function useReviewMutation<TInput>(fn: (input: TInput) => Promise<unknown>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: key })
      await queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}

export function useClaim(id: string) {
  return useReviewMutation(() => apiRequest(`/reviews/${id}/claim`, { method: 'POST' }))
}

export function useFieldAction(id: string) {
  return useReviewMutation((input: { fieldId: string; action: 'accept' | 'edit' | 'reject'; value?: unknown; reason?: string }) =>
    apiRequest(`/reviews/${id}/fields/${input.fieldId}`, {
      method: 'POST',
      body: {
        action: input.action,
        value: input.value,
        reason: input.reason,
      },
    }),
  )
}

export function useAddRow(id: string) {
  return useReviewMutation((input: { part_id: string; array_path: string; cells: Record<string, string> }) =>
    apiRequest(`/reviews/${id}/rows`, { method: 'POST', body: input }),
  )
}

export function useDeleteRow(id: string) {
  return useReviewMutation((input: { part_id: string; row_id: string }) =>
    apiRequest(`/reviews/${id}/rows/${input.row_id}?part_id=${input.part_id}`, {
      method: 'DELETE',
    }),
  )
}

export function useResolve(id: string) {
  return useReviewMutation((input: { decision: 'approve' | 'reject' | 'send-back'; text: string }) =>
    apiRequest(`/reviews/${id}/${input.decision}`, {
      method: 'POST',
      body: input.decision === 'approve' ? { note: input.text || null } : { reason: input.text },
    }),
  )
}
