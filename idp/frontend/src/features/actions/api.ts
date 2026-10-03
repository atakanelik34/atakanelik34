import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/lib/api'

export interface ActionRun {
  id: string
  job_id: string
  part_id: string
  name: string
  connection_key: string
  kind: string
  status: 'pending_approval' | 'approved' | 'rejected' | 'succeeded' | 'failed'
  requires_approval: boolean
  attempts: number
  payload: Record<string, unknown>
  external_reference: string | null
  error_code: string | null
  is_mock: boolean
  decided_by_id: string | null
  decided_at: string | null
  decision_note: string | null
  executed_at: string | null
  created_at: string
}

export interface ApiKeyInfo {
  id: string
  name: string
  prefix: string
  scopes: string[]
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

export interface OutboxEvent {
  id: string
  event_type: string
  aggregate_id: string
  created_at: string
  published_at: string | null
  attempts: number
  deliveries: Record<string, string>
  last_error: string | null
}

export function useActionRuns(documentId: string, poll: boolean) {
  return useQuery({
    queryKey: ['documents', 'actions', documentId],
    queryFn: ({ signal }) => apiRequest<ActionRun[]>(`/documents/${documentId}/actions`, { signal }),
    refetchInterval: poll ? 2_000 : false,
  })
}

export function useDecideActions(documentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { approve: boolean; text: string }) =>
      apiRequest<ActionRun[]>(`/documents/${documentId}/actions/${input.approve ? 'approve' : 'reject'}`, {
        method: 'POST',
        body: input.approve ? { note: input.text || null } : { reason: input.text },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}

export function useApiKeys(enabled: boolean) {
  return useQuery({
    queryKey: ['api-keys'],
    queryFn: ({ signal }) => apiRequest<ApiKeyInfo[]>('/api-keys', { signal }),
    enabled,
  })
}

export function useCreateApiKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string; expires_in_days: number | null }) =>
      apiRequest<ApiKeyInfo & { token: string }>('/api-keys', { method: 'POST', body: input }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
  })
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiRequest(`/api-keys/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
  })
}

export function useEvents(enabled: boolean) {
  return useQuery({
    queryKey: ['events'],
    queryFn: ({ signal }) => apiRequest<OutboxEvent[]>('/events?limit=50', { signal }),
    enabled,
  })
}
