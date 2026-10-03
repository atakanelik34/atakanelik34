import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  ACTIVE_STATUSES,
  type DocumentDetail,
  type DocumentList,
  type DocumentStatus,
  type Job,
  type Timeline,
  type UploadResponse,
} from '@/features/documents/types'
import { apiRequest } from '@/lib/api'

const documentsKey = ['documents'] as const
const ACTIVE_POLL_MS = 2_000

export function useDocuments(status: DocumentStatus | null, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [...documentsKey, 'list', status],
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: '25' })
      if (status) params.set('status', status)
      if (pageParam) params.set('cursor', pageParam)
      return apiRequest<DocumentList>(`/documents?${params.toString()}`, { signal })
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled,
    // Keep the list live while anything on the first page is still moving.
    refetchInterval: (query) =>
      query.state.data?.pages[0]?.items.some((d) => ACTIVE_STATUSES.includes(d.status))
        ? ACTIVE_POLL_MS
        : false,
  })
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: [...documentsKey, 'detail', id],
    queryFn: ({ signal }) => apiRequest<DocumentDetail>(`/documents/${id}`, { signal }),
    refetchInterval: (query) =>
      query.state.data && ACTIVE_STATUSES.includes(query.state.data.status) ? ACTIVE_POLL_MS : false,
  })
}

export function useTimeline(id: string, poll: boolean) {
  return useQuery({
    queryKey: [...documentsKey, 'timeline', id],
    queryFn: ({ signal }) => apiRequest<Timeline>(`/documents/${id}/timeline`, { signal }),
    refetchInterval: poll ? ACTIVE_POLL_MS : false,
  })
}

export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return apiRequest<UploadResponse>('/documents', { method: 'POST', body: form })
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  })
}

export function useReprocessDocument(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<Job>(`/documents/${id}/process`, { method: 'POST' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  })
}

export function useDeleteDocument(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<void>(`/documents/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  })
}

export async function openDownload(id: string): Promise<void> {
  const link = await apiRequest<{ url: string; expires_at: string }>(`/documents/${id}/download`)
  // Signed URL to the object store; opened without leaking our origin.
  window.open(link.url, '_blank', 'noopener,noreferrer')
}
