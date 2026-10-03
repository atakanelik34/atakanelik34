import { useQuery } from '@tanstack/react-query'

import { apiRequest } from '@/lib/api'

export interface ProviderDescriptor {
  kind: string
  name: string
  version: string
  method: string
  tier: number | null
  locality: 'local' | 'cloud'
  is_mock: boolean
  status: 'configured' | 'not_configured'
  cost_per_page: number
}

export interface ProvidersResponse {
  providers: ProviderDescriptor[]
  policy: {
    mode: 'LOCAL_ONLY' | 'HYBRID' | 'CLOUD_ALLOWED'
    allow_llm: boolean
    allow_mock_providers: boolean
    max_cost_per_document: number | null
    version: number
    routing_version: number
  }
}

export interface WorkflowDefinition {
  key: string
  version: number
  steps: string[]
  final_status: string
  is_default: boolean
}

export interface JobSummary {
  id: string
  document_id: string
  document_name: string
  workflow: string
  status: string
  trigger: string
  attempts: number
  max_attempts: number
  current_step: string | null
  last_error_code: string | null
  next_attempt_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: ({ signal }) => apiRequest<ProvidersResponse>('/providers', { signal }),
  })
}

export function useWorkflows() {
  return useQuery({
    queryKey: ['workflows'],
    queryFn: ({ signal }) => apiRequest<WorkflowDefinition[]>('/workflows', { signal }),
  })
}

export function useJobs(statuses: string[]) {
  return useQuery({
    queryKey: ['jobs', statuses],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ limit: '100' })
      for (const s of statuses) params.append('status', s)
      return apiRequest<JobSummary[]>(`/jobs?${params}`, { signal })
    },
    refetchInterval: 5_000,
  })
}
