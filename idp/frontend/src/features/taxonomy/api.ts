import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type {
  DocumentTypeDetail,
  DocumentTypeSummary,
  SchemaDefinition,
  SchemaVersion,
  Template,
} from '@/features/taxonomy/types'
import { apiRequest } from '@/lib/api'

const key = ['document-types'] as const

export function useDocumentTypes() {
  return useQuery({
    queryKey: key,
    queryFn: ({ signal }) => apiRequest<DocumentTypeSummary[]>('/document-types', { signal }),
  })
}

export function useTemplates() {
  return useQuery({
    queryKey: ['document-type-templates'],
    queryFn: ({ signal }) => apiRequest<Template[]>('/document-type-templates', { signal }),
    staleTime: Infinity,
  })
}

export function useDocumentType(id: string) {
  return useQuery({
    queryKey: [...key, id],
    queryFn: ({ signal }) => apiRequest<DocumentTypeDetail>(`/document-types/${id}`, { signal }),
  })
}

export function useSchemaVersion(id: string, version: number | null) {
  return useQuery({
    queryKey: [...key, id, 'version', version],
    queryFn: ({ signal }) => apiRequest<SchemaVersion>(`/document-types/${id}/schemas/${version}`, { signal }),
    enabled: version !== null,
  })
}

function useInvalidate() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: key })
}

export function useCreateFromTemplate() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (input: { template_key: string; publish: boolean }) =>
      apiRequest<DocumentTypeDetail>('/document-types/from-template', { method: 'POST', body: input }),
    onSuccess: invalidate,
  })
}

export function useCreateType() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (input: { key: string; name: string; description: string }) =>
      apiRequest<DocumentTypeDetail>('/document-types', { method: 'POST', body: input }),
    onSuccess: invalidate,
  })
}

export function useSaveDraft(id: string) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (definition: SchemaDefinition | null) =>
      apiRequest<SchemaVersion>(`/document-types/${id}/draft`, {
        method: 'PUT',
        body: definition ? { definition } : {},
      }),
    onSuccess: invalidate,
  })
}

export function usePublish(id: string) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: () => apiRequest<SchemaVersion>(`/document-types/${id}/publish`, { method: 'POST' }),
    onSuccess: invalidate,
  })
}

export function useUpdateType(id: string) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (input: { is_active?: boolean; name?: string }) =>
      apiRequest<DocumentTypeDetail>(`/document-types/${id}`, { method: 'PATCH', body: input }),
    onSuccess: invalidate,
  })
}
