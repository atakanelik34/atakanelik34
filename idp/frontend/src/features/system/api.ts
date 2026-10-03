import { useQuery } from '@tanstack/react-query'

import { apiRequest } from '@/lib/api'

export type HealthStatus = 'up' | 'degraded' | 'down'

export interface WorkerInfo {
  worker_id: string
  version: string
  max_jobs: number
  last_seen_at: number
  started_at: number
}

export interface ComponentStatus {
  name: string
  status: HealthStatus
  latency_ms: number | null
  detail: string | null
  metadata: Record<string, unknown>
}

export interface SystemStatus {
  status: HealthStatus
  environment: string
  version: string
  storage_backend: string
  components: ComponentStatus[]
}

export function useSystemStatus(enabled: boolean) {
  return useQuery({
    queryKey: ['system', 'status'],
    queryFn: ({ signal }) => apiRequest<SystemStatus>('/system/status', { signal }),
    enabled,
    refetchInterval: 15_000,
  })
}
