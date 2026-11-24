import { defineConfig } from 'vitest/config'

// 테스트는 Managed Runtime 플러그인 없이 순수 로직만 돌린다.
export default defineConfig({
  test: { include: ['src/**/*.test.ts'] },
})
