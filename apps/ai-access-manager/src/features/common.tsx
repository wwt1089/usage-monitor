import type { Member, SeatSummary, ServiceAdapter, ServiceId, ServiceSnapshot, Verdict } from '../domain/types'

export type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; snapshot: ServiceSnapshot }

export interface ChangeRequest {
  service: ServiceId
  action: '부여' | '회수'
  /** 부여 대상 이메일 */
  emails?: string[]
  /** 회수 대상 */
  members?: Member[]
}

const VERDICT_CLASS: Record<Verdict, string> = {
  '사용 중': 'ok',
  미사용: 'bad',
  '사용 이력 없음': 'warn',
  신규: 'info',
  '초대 대기': 'info',
  '판정 불가': 'muted',
}

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return <span className={`badge ${VERDICT_CLASS[verdict]}`}>{verdict}</span>
}

export function ModeBadge({ adapter }: { adapter: ServiceAdapter }) {
  return adapter.mode === 'live' ? (
    <span className="badge ok">실제 연결</span>
  ) : (
    <span className="badge demo" title="커넥터가 아직 연결되지 않아 예시 데이터를 보여 줍니다.">
      예시 데이터
    </span>
  )
}

export function ServiceDot({ service }: { service: ServiceId }) {
  return <span className={`dot dot-${service}`} aria-hidden="true" />
}

export function SeatBar({ seats }: { seats: SeatSummary }) {
  const taken = seats.used + seats.pending
  if (seats.total === null) {
    return <div className="seat-text">사용 {taken}석 · 전체 좌석 수는 서비스가 알려 주지 않습니다</div>
  }
  const ratio = seats.total > 0 ? Math.min(100, Math.round((taken / seats.total) * 100)) : 0
  return (
    <div>
      <div className="seat-bar" role="img" aria-label={`좌석 ${seats.total}석 중 ${taken}석 사용`}>
        <div className={`seat-fill${ratio >= 100 ? ' full' : ''}`} style={{ width: `${ratio}%` }} />
      </div>
      <div className="seat-text">
        {taken} / {seats.total}석 사용 · 남은 좌석 {Math.max(0, seats.total - taken)}
        {seats.pending > 0 ? ` · 초대 대기 ${seats.pending}` : ''}
      </div>
    </div>
  )
}
