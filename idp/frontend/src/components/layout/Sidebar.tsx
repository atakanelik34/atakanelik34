import { ScanLine } from 'lucide-react'
import { NavLink } from 'react-router'

import { NAV_GROUPS, type NavItem } from '@/components/layout/nav'
import { hasPermission } from '@/features/auth/api'
import type { Me } from '@/features/auth/types'
import { cn } from '@/lib/utils'

function NavEntry({ item }: { item: NavItem }) {
  const Icon = item.icon
  const base = 'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] [&_svg]:size-4'

  if (!item.to) {
    return (
      <span
        className={cn(base, 'cursor-default text-muted/60')}
        aria-disabled="true"
        title={`Available from phase ${item.plannedPhase}`}
      >
        <Icon aria-hidden />
        <span className="flex-1">{item.label}</span>
        <span className="font-mono text-[10px] text-muted/60">P{item.plannedPhase}</span>
      </span>
    )
  }

  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      className={({ isActive }) =>
        cn(
          base,
          isActive
            ? 'bg-accent-soft font-medium text-accent'
            : 'text-ink/80 hover:bg-canvas hover:text-ink',
        )
      }
    >
      <Icon aria-hidden />
      {item.label}
    </NavLink>
  )
}

export function Sidebar({ me }: { me: Me | undefined }) {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
        <span className="flex size-7 items-center justify-center rounded-md bg-accent text-white">
          <ScanLine aria-hidden className="size-4" />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold">IDP Platform</p>
          <p className="text-[11px] text-muted">{me?.tenant.name ?? ' '}</p>
        </div>
      </div>
      <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {NAV_GROUPS.map((group) => {
          const items = group.items.filter(
            (item) => !item.permission || hasPermission(me, item.permission),
          )
          if (items.length === 0) return null
          return (
            <div key={group.label}>
              <p className="mb-1.5 px-2.5 text-[10px] font-semibold tracking-wider text-muted uppercase">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {items.map((item) => (
                  <NavEntry key={item.label} item={item} />
                ))}
              </div>
            </div>
          )
        })}
      </nav>
    </aside>
  )
}
