export type Role = 'owner' | 'admin' | 'operator' | 'reviewer' | 'viewer'

export const ROLES: Role[] = ['owner', 'admin', 'operator', 'reviewer', 'viewer']

export type Permission =
  | 'documents:read'
  | 'documents:write'
  | 'reviews:read'
  | 'reviews:write'
  | 'config:read'
  | 'config:write'
  | 'users:read'
  | 'users:write'
  | 'audit:read'
  | 'system:read'
  | 'tenant:manage'

export interface User {
  id: string
  email: string
  full_name: string
  role: Role
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface Tenant {
  id: string
  slug: string
  name: string
}

export interface Me {
  user: User
  tenant: Tenant
  permissions: Permission[]
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
}
