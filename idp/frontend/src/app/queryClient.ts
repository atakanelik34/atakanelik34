import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/lib/api'

const NON_RETRYABLE = new Set(['AUTHENTICATION_ERROR', 'AUTHORIZATION_ERROR', 'NOT_FOUND', 'VALIDATION_ERROR'])

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) =>
          !(error instanceof ApiError && NON_RETRYABLE.has(error.category)) && failureCount < 2,
        refetchOnWindowFocus: false,
      },
    },
  })
}
