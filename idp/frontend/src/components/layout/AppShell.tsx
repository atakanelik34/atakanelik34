import { useQueryClient } from '@tanstack/react-query'
import { LogOut } from 'lucide-react'
import { Outlet } from 'react-router'

import { Sidebar } from '@/components/layout/Sidebar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useMe } from '@/features/auth/api'
import { session } from '@/lib/session'

export function AppShell() {
  const { data: me } = useMe()
  const queryClient = useQueryClient()

  function signOut() {
    session.clear()
    queryClient.clear()
  }

  return (
    <div className="flex h-full">
      <Sidebar me={me} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-end gap-3 border-b border-border bg-surface px-6">
          {me ? (
            <>
              <div className="text-right leading-tight">
                <p className="text-[13px] font-medium">{me.user.full_name}</p>
                <p className="text-[11px] text-muted">{me.user.email}</p>
              </div>
              <Badge tone="accent">{me.user.role}</Badge>
            </>
          ) : null}
          <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out" title="Sign out">
            <LogOut />
          </Button>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-6 py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
