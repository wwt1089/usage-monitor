import { SERVICE_LABEL } from '../domain/types'
import type { ChangeRequest } from './common'

interface Props {
  request: ChangeRequest
  simulate: boolean
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** 변경 전에 대상을 그대로 보여 주고 확인받는다. */
export function ConfirmDialog({ request, simulate, busy, onConfirm, onCancel }: Props) {
  const targets = request.action === '부여' ? (request.emails ?? []) : (request.members ?? []).map((m) => m.email)
  return (
    <div className="overlay" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">
          {SERVICE_LABEL[request.service]} · {targets.length}명 {request.action}
        </h2>
        {simulate ? (
          <p className="callout info">시뮬레이션이 켜져 있습니다. 실제로는 아무것도 바뀌지 않고 기록만 남습니다.</p>
        ) : (
          <p className="callout bad">
            실제 적용입니다. 확인을 누르면 {SERVICE_LABEL[request.service]}에 바로 반영됩니다.
          </p>
        )}
        <ul className="target-list">
          {targets.map((email) => (
            <li key={email}>{email}</li>
          ))}
        </ul>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} disabled={busy}>
            취소
          </button>
          <button type="button" className={simulate ? 'primary' : 'danger'} onClick={onConfirm} disabled={busy}>
            {busy ? '처리 중…' : simulate ? '시뮬레이션 실행' : `${request.action} 실행`}
          </button>
        </div>
      </div>
    </div>
  )
}
