/** 화면과 연결 계층이 함께 쓰는 공통 모델. 서비스마다 다른 응답을 이 형태로 맞춘다. */

export type ServiceId = 'm365' | 'claude' | 'cursor' | 'chatgpt'

export const SERVICE_ORDER: ServiceId[] = ['m365', 'claude', 'cursor', 'chatgpt']

export const SERVICE_LABEL: Record<ServiceId, string> = {
  m365: 'Microsoft 365 Copilot',
  claude: 'Claude',
  cursor: 'Cursor',
  chatgpt: 'ChatGPT',
}

export type MemberStatus = 'active' | 'pending'

export interface Member {
  /** 서비스가 준 고유 ID. 회수 호출에 쓴다. */
  id: string
  email: string
  name: string
  role: string
  status: MemberStatus
  /** 권한이 생긴 날 (YYYY-MM-DD). 모르면 비운다. */
  addedAt?: string
  /**
   * 마지막 사용일 (YYYY-MM-DD).
   * null = 서비스가 "사용 이력 없음"이라고 답함, undefined = 서비스가 값을 주지 않아 판정 불가.
   */
  lastActivity?: string | null
  /** 부가 정보 한 줄 (SKU, 할당 방식, 지출 등). */
  detail?: string
  /** false 면 이 앱에서 회수할 수 없다. 이유는 revokeBlockedReason 에 적는다. */
  revocable: boolean
  revokeBlockedReason?: string
}

export interface SeatSummary {
  /** 구매한 좌석 수. 서비스가 알려 주지 않으면 null. */
  total: number | null
  used: number
  pending: number
}

export interface Capabilities {
  canGrant: boolean
  canRevoke: boolean
  /** 부여 버튼 이름. 서비스마다 '라이선스 부여', '초대' 등으로 다르다. */
  grantLabel: string
  revokeLabel: string
  /** 화면에 그대로 보여 줄 제약 사항. */
  notes: string[]
}

export interface ServiceSnapshot {
  members: Member[]
  seats: SeatSummary
  /** 사용 데이터 기준일 (YYYY-MM-DD). 없으면 오늘로 본다. */
  asOf?: string
  warnings: string[]
}

export interface ActionResult {
  ok: boolean
  message: string
}

export interface ServiceAdapter {
  id: ServiceId
  /** demo = 예시 데이터, live = 커넥터로 실제 서비스 호출 */
  mode: 'demo' | 'live'
  capabilities: Capabilities
  load(): Promise<ServiceSnapshot>
  grant(email: string): Promise<ActionResult>
  revoke(member: Member): Promise<ActionResult>
}

export type Verdict = '사용 중' | '미사용' | '사용 이력 없음' | '신규' | '초대 대기' | '판정 불가'

export interface LogEntry {
  at: string
  actor: string
  service: ServiceId
  action: '부여' | '회수'
  target: string
  outcome: '성공' | '실패' | '시뮬레이션'
  message: string
}
