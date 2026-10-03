import { createBrowserRouter, Navigate } from 'react-router'

import { AppShell } from '@/components/layout/AppShell'
import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { DocumentDetailPage } from '@/features/documents/DocumentDetailPage'
import { DocumentsPage } from '@/features/documents/DocumentsPage'
import { DatasetPage } from '@/features/evaluation/DatasetPage'
import { EvaluationPage } from '@/features/evaluation/EvaluationPage'
import { ProcessingPage } from '@/features/processing/ProcessingPage'
import { ProvidersPage } from '@/features/processing/ProvidersPage'
import { WorkflowsPage } from '@/features/processing/WorkflowsPage'
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
      { path: 'processing', element: <ProcessingPage /> },
      { path: 'workflows', element: <WorkflowsPage /> },
      { path: 'providers', element: <ProvidersPage /> },
      { path: 'evaluation', element: <EvaluationPage /> },
      { path: 'evaluation/:id', element: <DatasetPage /> },
      { path: 'users', element: <UsersPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]

export function createRouter() {
  return createBrowserRouter(routes)
}
