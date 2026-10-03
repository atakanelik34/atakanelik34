import { useQuery } from '@tanstack/react-query'

import type { ProcessingResult } from '@/features/extraction/types'
import { apiRequest } from '@/lib/api'

export function useProcessingResult(documentId: string, poll: boolean) {
  return useQuery({
    queryKey: ['documents', 'extraction', documentId],
    queryFn: ({ signal }) => apiRequest<ProcessingResult>(`/documents/${documentId}/extraction`, { signal }),
    refetchInterval: poll ? 2_000 : false,
  })
}
