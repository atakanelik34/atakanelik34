import { CircleX, LoaderCircle } from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'

import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

export function Skeleton({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn('animate-pulse rounded-md bg-border/60', className)} {...props} />
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle aria-hidden className={cn('size-4 animate-spin', className)} />
}

/** Renders a classified API error with its correlation id for support. */
export function ErrorNotice({ error, title }: { error: unknown; title?: ReactNode }) {
  const apiError = error instanceof ApiError ? error : null
  const message = error instanceof Error ? error.message : 'Unexpected error'
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-md border border-danger/20 bg-danger-soft px-4 py-3 text-sm text-danger"
    >
      <CircleX aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="font-medium">{title ?? message}</p>
        {title ? <p className="mt-0.5">{message}</p> : null}
        {apiError ? (
          <p className="mt-1 font-mono text-[11px] opacity-80">
            {apiError.category}
            {apiError.correlationId ? ` · ref ${apiError.correlationId}` : ''}
          </p>
        ) : null}
      </div>
    </div>
  )
}
