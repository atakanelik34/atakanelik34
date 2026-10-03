import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/lib/api'

export type ConnectionKind = 'master_data' | 'rest' | 'mock_erp'

export interface ConnectionInfo {
  id: string
  key: string
  name: string
  kind: ConnectionKind
  config: Record<string, unknown>
  is_active: boolean
  is_mock: boolean
  records: number
  created_at: string
}

export interface MasterRecord {
  key: string
  entity: string
  name: string
  attributes: Record<string, string>
}

export interface LookupMatch {
  key: string
  name: string
  attributes: Record<string, unknown>
  score: number
  matched_on: string[]
}

export interface LookupResult {
  status: 'matched' | 'not_found' | 'ambiguous'
  best: LookupMatch | null
  candidates: LookupMatch[]
}

const key = ['connections'] as const

export function useConnections() {
  return useQuery({ queryKey: key, queryFn: ({ signal }) => apiRequest<ConnectionInfo[]>('/connections', { signal }) })
}

export function useConnection(id: string) {
  return useQuery({
    queryKey: [...key, id],
    queryFn: ({ signal }) => apiRequest<ConnectionInfo>(`/connections/${id}`, { signal }),
  })
}

export function useRecords(id: string, q: string) {
  return useQuery({
    queryKey: [...key, id, 'records', q],
    queryFn: ({ signal }) =>
      apiRequest<{ items: MasterRecord[]; total: number }>(`/connections/${id}/records?limit=50&q=${encodeURIComponent(q)}`, { signal }),
  })
}

function useConnectionMutation<TInput, TOutput>(fn: (input: TInput) => Promise<TOutput>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: key })
    },
  })
}

export function useCreateConnection() {
  return useConnectionMutation((input: { key: string; name: string; kind: ConnectionKind; config: Record<string, unknown> }) =>
    apiRequest<ConnectionInfo>('/connections', { method: 'POST', body: input }),
  )
}

export function useUpdateConnection(id: string) {
  return useConnectionMutation((input: { is_active?: boolean; name?: string; config?: Record<string, unknown> }) =>
    apiRequest<ConnectionInfo>(`/connections/${id}`, { method: 'PATCH', body: input }),
  )
}

export function useImportRecords(id: string) {
  return useConnectionMutation((file: File) => {
    const form = new FormData()
    form.append('file', file)
    return apiRequest<{ imported: number; skipped: number; errors: string[] }>(`/connections/${id}/records/import?entity=vendor`, {
      method: 'POST',
      body: form,
    })
  })
}

export function useTestLookup(id: string) {
  return useMutation({
    mutationFn: (criteria: Record<string, string>) =>
      apiRequest<LookupResult>(`/connections/${id}/test`, { method: 'POST', body: { criteria } }),
  })
}
