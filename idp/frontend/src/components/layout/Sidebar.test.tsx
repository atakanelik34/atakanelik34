import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Sidebar } from '@/components/layout/Sidebar'
import type { Me, Permission } from '@/features/auth/types'
import { renderRoutes } from '@/test/render'

function me(permissions: Permission[]): Me {
  return {
    user: {
      id: 'u1',
      email: 'a@acme.test',
      full_name: 'A',
      role: 'viewer',
      is_active: true,
      last_login_at: null,
      created_at: '',
    },
    tenant: { id: 't1', slug: 'acme', name: 'Acme Ltd' },
    permissions,
  }
}

describe('Sidebar', () => {
  it('hides sections the role cannot access', () => {
    renderRoutes([{ path: '/', element: <Sidebar me={me(['documents:read'])} /> }], '/')
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Acme Ltd')).toBeInTheDocument()
  })

  it('shows planned sections as disabled with their phase, not as fake pages', () => {
    renderRoutes([{ path: '/', element: <Sidebar me={me(['users:read'])} /> }], '/')
    expect(screen.getByRole('link', { name: 'Users' })).toBeInTheDocument()
    const inbox = screen.getByText('Inbox').closest('[aria-disabled="true"]')
    expect(inbox).not.toBeNull()
    expect(inbox).toHaveTextContent('P11')
    expect(screen.queryByRole('link', { name: /Inbox/ })).not.toBeInTheDocument()
  })
})
