import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom has no object URLs; the viewer turns page images into blob: URLs.
let objectUrls = 0
URL.createObjectURL = () => `blob:test/${++objectUrls}`
URL.revokeObjectURL = () => undefined

afterEach(() => {
  cleanup()
  sessionStorage.clear()
})
