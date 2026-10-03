import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/lib/api'

export interface Metrics {
  compared: number
  tp: number
  fp: number
  fn: number
  precision: number | null
  recall: number | null
  f1: number | null
  exact_match: number | null
  normalized_match: number | null
  mean_confidence: number | null
  mean_confidence_correct: number | null
  mean_confidence_incorrect: number | null
  overconfident: number
}

export interface RunMetrics extends Metrics {
  items: number
  items_without_result: number
  intervention_rate: number | null
  cost_per_document: number | null
  latency_ms_per_document: number | null
}

export interface RunSummary {
  id: string
  status: string
  created_at: string
  finished_at: string | null
  metrics: RunMetrics
  config: Record<string, string[]>
}

export interface Run extends RunSummary {
  dataset_id: string
  field_metrics: Record<string, Metrics>
  item_results: {
    item_id: string
    status: 'scored' | 'no_result'
    document_id?: string
    pages?: [number, number]
    correct?: number
    compared?: number
    needed_review?: boolean
    wrong_fields?: string[]
  }[]
}

export interface Dataset {
  id: string
  name: string
  description: string
  document_type: string | null
  items: number
  created_at: string
  last_run: RunSummary | null
}

const key = ['evaluation'] as const

export function useDatasets() {
  return useQuery({
    queryKey: [...key, 'datasets'],
    queryFn: ({ signal }) => apiRequest<Dataset[]>('/evaluation/datasets', { signal }),
  })
}

export function useDataset(id: string) {
  return useQuery({
    queryKey: [...key, 'datasets', id],
    queryFn: ({ signal }) => apiRequest<Dataset>(`/evaluation/datasets/${id}`, { signal }),
  })
}

export function useRuns(datasetId: string) {
  return useQuery({
    queryKey: [...key, 'runs', datasetId],
    queryFn: ({ signal }) => apiRequest<RunSummary[]>(`/evaluation/datasets/${datasetId}/runs`, { signal }),
  })
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: [...key, 'run', runId],
    queryFn: ({ signal }) => apiRequest<Run>(`/evaluation/runs/${runId}`, { signal }),
    enabled: runId !== null,
  })
}

function useEvaluationMutation<TInput, TOutput>(fn: (input: TInput) => Promise<TOutput>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: key })
    },
  })
}

export function useCreateDataset() {
  return useEvaluationMutation((input: { name: string; description: string; document_type: string | null }) =>
    apiRequest<Dataset>('/evaluation/datasets', { method: 'POST', body: input }),
  )
}

export function useImportReviews(datasetId: string) {
  return useEvaluationMutation(() =>
    apiRequest<{ added: number }>(`/evaluation/datasets/${datasetId}/import-reviews`, { method: 'POST' }),
  )
}

export function useStartRun(datasetId: string) {
  return useEvaluationMutation(() => apiRequest<Run>(`/evaluation/datasets/${datasetId}/runs`, { method: 'POST' }))
}
