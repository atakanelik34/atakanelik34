import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAccessToken } from '@/features/auth/api'

export function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAccessToken()
  const location = useLocation()
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}
