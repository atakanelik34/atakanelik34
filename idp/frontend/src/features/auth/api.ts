import { useQuery } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'

import type { Me, Permission, TokenResponse } from '@/features/auth/types'
import { apiRequest } from '@/lib/api'
import { session } from '@/lib/session'

export const meQueryKey = ['auth', 'me'] as const

export async function login(email: string, password: string): Promise<TokenResponse> {
  return apiRequest<TokenResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
    anonymous: true,
  })
}

export function useAccessToken(): string | null {
  return useSyncExternalStore(session.subscribe, session.getToken, session.getToken)
}

export function useMe() {
  const token = useAccessToken()
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => apiRequest<Me>('/auth/me', { signal }),
    enabled: token !== null,
    staleTime: 60_000,
  })
}

export function hasPermission(me: Me | undefined, permission: Permission): boolean {
  return me?.permissions.includes(permission) ?? false
}
