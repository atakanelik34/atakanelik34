import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Role, User } from '@/features/auth/types'
import { apiRequest } from '@/lib/api'

const usersKey = ['users'] as const

export interface CreateUserInput {
  email: string
  full_name: string
  role: Role
  password: string
}

export function useUsers(enabled: boolean) {
  return useQuery({
    queryKey: usersKey,
    queryFn: ({ signal }) => apiRequest<User[]>('/users', { signal }),
    enabled,
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateUserInput) =>
      apiRequest<User>('/users', { method: 'POST', body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: usersKey }),
  })
}
