import type { Capabilities, ServiceAdapter, ServiceId } from '../domain/types'
import { SERVICE_ORDER } from '../domain/types'
import { createDemoAdapter } from './demo/adapter'
import { CHATGPT_CAPABILITIES, createChatgptAdapter } from './live/chatgpt'
import { CLAUDE_CAPABILITIES, createClaudeAdapter } from './live/claude'
import { createCursorAdapter, CURSOR_CAPABILITIES } from './live/cursor'
import { createM365Adapter, M365_CAPABILITIES } from './live/m365'

const LIVE: Record<ServiceId, () => ServiceAdapter | null> = {
  m365: createM365Adapter,
  claude: createClaudeAdapter,
  cursor: createCursorAdapter,
  chatgpt: createChatgptAdapter,
}

const CAPABILITIES: Record<ServiceId, Capabilities> = {
  m365: M365_CAPABILITIES,
  claude: CLAUDE_CAPABILITIES,
  cursor: CURSOR_CAPABILITIES,
  chatgpt: CHATGPT_CAPABILITIES,
}

/**
 * 서비스마다 커넥터 코드(generated/)가 있으면 실제 연결을, 없으면 예시 데이터 연결을 쓴다.
 * 그래서 커넥터를 하나씩 붙여 가며 확인할 수 있다.
 */
export function createAdapters(): Record<ServiceId, ServiceAdapter> {
  const adapters = {} as Record<ServiceId, ServiceAdapter>
  for (const id of SERVICE_ORDER) {
    adapters[id] = LIVE[id]() ?? createDemoAdapter(id, CAPABILITIES[id])
  }
  return adapters
}
