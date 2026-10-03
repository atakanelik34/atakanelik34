import './index.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'

import { createQueryClient } from '@/app/queryClient'
import { createRouter } from '@/app/router'

const root = document.getElementById('root')
if (!root) throw new Error('Root element #root not found')

const queryClient = createQueryClient()
const router = createRouter()

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
