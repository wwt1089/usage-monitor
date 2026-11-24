/** 빌드할 때 .env.local 에서 읽는 설정. 비밀 값은 여기에 두지 않는다(키는 커넥터 연결에 저장한다). */

function read(name: string): string {
  const value = (import.meta.env as Record<string, unknown>)[name]
  return typeof value === 'string' ? value.trim() : ''
}

export const config = {
  /** ChatGPT 워크스페이스 ID. 실제 연결에서 사용자 목록을 읽을 때 필요하다. */
  chatgptWorkspaceId: read('VITE_CHATGPT_WORKSPACE_ID'),
  /** Copilot SKU 가 여러 개일 때 부여에 쓸 SKU 이름(예: Microsoft_365_Copilot). */
  m365GrantSku: read('VITE_M365_GRANT_SKU'),
}
