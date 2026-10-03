import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'

import { apiRequest } from '@/lib/api'

export type BBox = [number, number, number, number]

export interface PageImage {
  page_number: number
  url: string
  width: number
  height: number
  expires_at: string
}

export interface LayoutWord {
  text: string
  bbox: BBox
  confidence: number | null
}

export interface LayoutLine {
  id: string
  text: string
  bbox: BBox
  words: LayoutWord[]
}

export interface PageLayout {
  page_number: number
  source: string
  text_quality: number
  language: string | null
  ocr_confidence: number | null
  lines: LayoutLine[]
}

// Signed URLs live 5 minutes server-side; refresh well before expiry.
const IMAGE_STALE_MS = 3 * 60_000

export function usePageImage(documentId: string, page: number) {
  return useQuery({
    queryKey: ['documents', 'page-image', documentId, page],
    queryFn: ({ signal }) =>
      apiRequest<PageImage>(`/documents/${documentId}/pages/${page}/image`, { signal }),
    staleTime: IMAGE_STALE_MS,
    refetchInterval: IMAGE_STALE_MS,
  })
}

/**
 * The page image as a same-origin blob URL. Images are fetched through the API
 * (with the session's bearer token) rather than from object storage, so the
 * Content-Security-Policy can stay `img-src 'self' blob:` in every deployment.
 */
export function usePageImageSrc(documentId: string, page: number) {
  const blob = useQuery({
    queryKey: ['documents', 'page-image-content', documentId, page],
    queryFn: ({ signal }) =>
      apiRequest<Blob>(`/documents/${documentId}/pages/${page}/image/content`, { signal, responseType: 'blob' }),
    staleTime: Infinity,
  })
  const src = useMemo(() => (blob.data ? URL.createObjectURL(blob.data) : null), [blob.data])
  useEffect(
    () => () => {
      if (src) URL.revokeObjectURL(src)
    },
    [src],
  )
  return { src, isError: blob.isError, error: blob.error }
}

export function usePageLayout(documentId: string, page: number, enabled: boolean) {
  return useQuery({
    queryKey: ['documents', 'page-layout', documentId, page],
    queryFn: ({ signal }) =>
      apiRequest<PageLayout>(`/documents/${documentId}/pages/${page}/layout`, { signal }),
    enabled,
    staleTime: Infinity,
  })
}
