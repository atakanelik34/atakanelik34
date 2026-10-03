import { createBrowserRouter, Navigate } from 'react-router'

import { AppShell } from '@/components/layout/AppShell'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { DocumentDetailPage } from '@/features/documents/DocumentDetailPage'
import { DocumentsPage } from '@/features/documents/DocumentsPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { UsersPage } from '@/features/users/UsersPage'
import { AuditLogPage } from '@/features/audit/AuditLogPage'
import { ReviewQueuePage } from '@/features/review/ReviewQueuePage'
import { ReviewWorkspacePage } from '@/features/review/ReviewWorkspacePage'
import { DocumentTypeDetailPage } from '@/features/taxonomy/DocumentTypeDetailPage'
import { DocumentTypesPage } from '@/features/taxonomy/DocumentTypesPage'

export const routes = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'documents', element: <DocumentsPage /> },
      { path: 'documents/:id', element: <DocumentDetailPage /> },
      { path: 'document-types', element: <DocumentTypesPage /> },
      { path: 'document-types/:id', element: <DocumentTypeDetailPage /> },
      { path: 'reviews', element: <ReviewQueuePage /> },
      { path: 'reviews/:id', element: <ReviewWorkspacePage /> },
      { path: 'audit', element: <AuditLogPage /> },
      { path: 'users', element: <UsersPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]

export function createRouter() {
  return createBrowserRouter(routes)
}
