import { useMemo, useState } from 'react'
import type { Member, ServiceAdapter, Verdict } from '../domain/types'
import { SERVICE_LABEL } from '../domain/types'
import { isCandidate, judge, todayIso } from '../domain/inactivity'
import { lastActivityText } from '../domain/format'
import { splitEmails } from '../domain/people'
import { ModeBadge, SeatBar, VerdictBadge, type ChangeRequest, type LoadState } from './common'

interface Props {
  adapter: ServiceAdapter
  state: LoadState
  inactiveDays: number
  onChange: (request: ChangeRequest) => void
  onReload: () => void
}

type Filter = '전체' | '정리 후보' | '사용 중' | '기타'

function matches(filter: Filter, verdict: Verdict): boolean {
  if (filter === '전체') return true
  if (filter === '정리 후보') return isCandidate(verdict)
  if (filter === '사용 중') return verdict === '사용 중'
  return !isCandidate(verdict) && verdict !== '사용 중'
}

export function ServicePage({ adapter, state, inactiveDays, onChange, onReload }: Props) {
  const [filter, setFilter] = useState<Filter>('전체')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [grantInput, setGrantInput] = useState('')
  const { capabilities } = adapter
  const label = SERVICE_LABEL[adapter.id]

  const asOf = state.status === 'ready' ? (state.snapshot.asOf ?? todayIso()) : todayIso()
  const rows = useMemo(() => {
    if (state.status !== 'ready') return []
    return state.snapshot.members.map((member) => ({ member, verdict: judge(member, inactiveDays, asOf) }))
  }, [state, inactiveDays, asOf])

  const needle = query.trim().toLowerCase()
  const visible = rows.filter(
    ({ member, verdict }) =>
      matches(filter, verdict) &&
      (!needle || member.email.includes(needle) || member.name.toLowerCase().includes(needle)),
  )
  // 목록을 새로 읽으면 사라진 대상이 있을 수 있어, 지금 목록에 있는 것만 선택으로 본다.
  const selectedMembers: Member[] = rows.filter(({ member }) => selected.includes(member.id)).map((r) => r.member)
  const grant = splitEmails(grantInput)

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]))
  }

  function selectInactive() {
    // 일괄 선택은 '미사용'만. '사용 이력 없음'은 사람이 하나씩 확인하고 고른다.
    setSelected(rows.filter(({ member, verdict }) => verdict === '미사용' && member.revocable).map((r) => r.member.id))
  }

  return (
    <section>
      <div className="page-head">
        <h2>{label}</h2>
        <ModeBadge adapter={adapter} />
        <button type="button" className="ghost" onClick={onReload} disabled={state.status === 'loading'}>
          새로고침
        </button>
      </div>

      {state.status === 'loading' && <p className="muted">불러오는 중…</p>}
      {state.status === 'error' && <p className="callout bad">불러오지 못했습니다: {state.message}</p>}

      {state.status === 'ready' && (
        <>
          <div className="panel">
            <SeatBar seats={state.snapshot.seats} />
            {state.snapshot.asOf && <p className="muted small">사용 데이터 기준일 {state.snapshot.asOf}</p>}
            {state.snapshot.warnings.map((warning) => (
              <p key={warning} className="callout warn">
                {warning}
              </p>
            ))}
          </div>

          <div className="toolbar">
            <div className="segmented" role="group" aria-label="목록 필터">
              {(['전체', '정리 후보', '사용 중', '기타'] as Filter[]).map((name) => (
                <button
                  type="button"
                  key={name}
                  className={filter === name ? 'on' : ''}
                  aria-pressed={filter === name}
                  onClick={() => setFilter(name)}
                >
                  {name} {rows.filter((r) => matches(name, r.verdict)).length}
                </button>
              ))}
            </div>
            <input
              type="search"
              placeholder="이름 또는 이메일 검색"
              aria-label="이름 또는 이메일 검색"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <span className="spacer" />
            {capabilities.canRevoke && (
              <>
                <button type="button" onClick={selectInactive}>
                  미사용자 선택
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={selectedMembers.length === 0}
                  onClick={() => onChange({ service: adapter.id, action: '회수', members: selectedMembers })}
                >
                  선택 {selectedMembers.length}명 {capabilities.revokeLabel}
                </button>
              </>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th aria-label="선택" />
                  <th>사용자</th>
                  <th>역할</th>
                  <th>부가 정보</th>
                  <th>권한 받은 날</th>
                  <th>마지막 사용일</th>
                  <th>상태</th>
                </tr>
              </thead>
              <tbody>
                {visible.map(({ member, verdict }) => (
                  <tr key={member.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`${member.email} 선택`}
                        checked={selected.includes(member.id)}
                        disabled={!member.revocable || !capabilities.canRevoke}
                        title={member.revocable ? undefined : member.revokeBlockedReason}
                        onChange={() => toggle(member.id)}
                      />
                    </td>
                    <td>
                      <div className="strong">{member.name || member.email}</div>
                      {member.name && <div className="muted small">{member.email}</div>}
                    </td>
                    <td>{member.role}</td>
                    <td>
                      {member.detail ?? '—'}
                      {!member.revocable && member.revokeBlockedReason && (
                        <div className="muted small">🔒 {member.revokeBlockedReason}</div>
                      )}
                    </td>
                    <td>{member.addedAt ?? '—'}</td>
                    <td>{lastActivityText(member, asOf)}</td>
                    <td>
                      <VerdictBadge verdict={verdict} />
                    </td>
                  </tr>
                ))}
                {visible.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted center">
                      조건에 맞는 사용자가 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="two-col">
            <div className="panel">
              <h3>{capabilities.grantLabel}</h3>
              {capabilities.canGrant ? (
                <>
                  <textarea
                    rows={3}
                    placeholder="이메일을 줄바꿈이나 쉼표로 구분해 입력"
                    aria-label={`${capabilities.grantLabel} 대상 이메일`}
                    value={grantInput}
                    onChange={(event) => setGrantInput(event.target.value)}
                  />
                  {grant.ignored.length > 0 && (
                    <p className="error-text small">이메일 형식이 아니어서 제외: {grant.ignored.join(', ')}</p>
                  )}
                  <button
                    type="button"
                    className="primary"
                    disabled={grant.emails.length === 0}
                    onClick={() => onChange({ service: adapter.id, action: '부여', emails: grant.emails })}
                  >
                    {grant.emails.length}명 {capabilities.grantLabel}
                  </button>
                </>
              ) : (
                <p className="muted">이 서비스는 이 화면에서 추가할 수 없습니다. 아래 안내를 확인하세요.</p>
              )}
            </div>
            <div className="panel">
              <h3>알아 둘 점</h3>
              <ul className="notes">
                {capabilities.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
