import {
  Activity,
  ClipboardCheck,
  Cpu,
  FileText,
  Inbox,
  LayoutDashboard,
  Plug,
  ScrollText,
  Settings,
  Shapes,
  Users,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

import type { Permission } from '@/features/auth/types'

export interface NavItem {
  label: string
  icon: LucideIcon
  /** Route path when the section is implemented. */
  to?: string
  /** Roadmap phase that delivers the section (ARCHITECTURE.md §16). */
  plannedPhase?: number
  permission?: Permission
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Operate',
    items: [
      { label: 'Dashboard', icon: LayoutDashboard, to: '/' },
      { label: 'Documents', icon: FileText, to: '/documents', permission: 'documents:read' },
      { label: 'Inbox', icon: Inbox, plannedPhase: 10 },
      { label: 'Processing', icon: Activity, plannedPhase: 8 },
      { label: 'Review queue', icon: ClipboardCheck, to: '/reviews', permission: 'reviews:read' },
    ],
  },
  {
    label: 'Configure',
    items: [
      { label: 'Document types', icon: Shapes, to: '/document-types', permission: 'config:read' },
      { label: 'Workflows', icon: Workflow, plannedPhase: 8 },
      { label: 'Connections', icon: Plug, plannedPhase: 10 },
      { label: 'Providers & models', icon: Cpu, plannedPhase: 8 },
    ],
  },
  {
    label: 'Govern',
    items: [
      { label: 'Users', icon: Users, to: '/users', permission: 'users:read' },
      { label: 'Audit log', icon: ScrollText, to: '/audit', permission: 'audit:read' },
      { label: 'Settings', icon: Settings, to: '/settings' },
    ],
  },
]
