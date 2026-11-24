import { useState } from 'react'
import type { ServiceAdapter, ServiceId } from '../domain/types'
import { SERVICE_LABEL, SERVICE_ORDER } from '../domain/types'
import { lastActivityText } from '../domain/format'
import { judge, todayIso } from '../domain/inactivity'
import type { Person } from '../domain/people'
import { ServiceDot, VerdictBadge, type ChangeRequest, type LoadState } from './common'

interface Props {
  adapters: Record<ServiceId, ServiceAdapter>
  states: Record<ServiceId, LoadState>
  people: Person[]
  inactiveDays: number
  onChange: (request: ChangeRequest) => void
}

/** 한 사람이 어떤 서비스 권한을 갖고 있는지 한 줄로 본다. 입사·퇴사·부서 이동 처리에 쓴다. */
export function PeoplePage({ adapters, states, people, inactiveDays, onChange }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const needle = query.trim().toLowerCase()
  const visible = people.filter((p) => !needle || p.email.includes(needle) || p.name.toLowerCase().includes(needle))

  function asOf(service: ServiceId): string {
    const state = states[service]
    return state.status === 'ready' ? (state.snapshot.asOf ?? todayIso()) : todayIso()
  }

  return (
    <section>
      <div className="page-head">
        <h2>사용자별 권한</h2>
      </div>
      <div className="toolbar">
        <input
          type="search"
          placeholder="이름 또는 이메일 검색"
          aria-label="이름 또는 이메일 검색"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="muted small">{visible.length}명 · 행을 누르면 서비스별로 부여·회수할 수 있습니다</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>사용자</th>
              {SERVICE_ORDER.map((id) => (
                <th key={id}>
                  <ServiceDot service={id} />
                  {SERVICE_LABEL[id]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((person) => (
              <PersonRows
                key={person.email}
                person={person}
                expanded={open === person.email}
                onToggle={() => setOpen(open === person.email ? null : person.email)}
                adapters={adapters}
                inactiveDays={inactiveDays}
                asOf={asOf}
                onChange={onChange}
              />
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={5} className="muted center">
                  조건에 맞는 사용자가 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

interface RowProps {
  person: Person
  expanded: boolean
  onToggle: () => void
  adapters: Record<ServiceId, ServiceAdapter>
  inactiveDays: number
  asOf: (service: ServiceId) => string
  onChange: (request: ChangeRequest) => void
}

function PersonRows({ person, expanded, onToggle, adapters, inactiveDays, asOf, onChange }: RowProps) {
  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td>
          <button type="button" className="link" aria-expanded={expanded} onClick={(e) => { e.stopPropagation(); onToggle() }}>
            {expanded ? '▾' : '▸'} {person.name || person.email}
          </button>
          {person.name && <div className="muted small indent">{person.email}</div>}
        </td>
        {SERVICE_ORDER.map((id) => {
          const member = person.access[id]
          return (
            <td key={id}>
              {member ? <VerdictBadge verdict={judge(member, inactiveDays, asOf(id))} /> : <span className="muted">—</span>}
            </td>
          )
        })}
      </tr>
      {expanded && (
        <tr className="detail-row">
          <td colSpan={5}>
            <div className="detail-grid">
              {SERVICE_ORDER.map((id) => {
                const member = person.access[id]
                const caps = adapters[id].capabilities
                return (
                  <div key={id} className="detail-item">
                    <div className="strong">
                      <ServiceDot service={id} />
                      {SERVICE_LABEL[id]}
                    </div>
                    {member ? (
                      <>
                        <div className="small">
                          {member.role}
                          {member.detail ? ` · ${member.detail}` : ''}
                        </div>
                        <div className="small muted">마지막 사용 {lastActivityText(member, asOf(id))}</div>
                        <button
                          type="button"
                          className="danger small-btn"
                          disabled={!caps.canRevoke || !member.revocable}
                          title={member.revocable ? undefined : member.revokeBlockedReason}
                          onClick={() => onChange({ service: id, action: '회수', members: [member] })}
                        >
                          {caps.revokeLabel}
                        </button>
                        {!caps.canRevoke ? (
                          <div className="small muted">🔒 이 서비스는 아직 이 화면에서 회수할 수 없습니다.</div>
                        ) : (
                          !member.revocable &&
                          member.revokeBlockedReason && <div className="small muted">🔒 {member.revokeBlockedReason}</div>
                        )}
                      </>
                    ) : (
                      <>
                        <div className="small muted">권한 없음</div>
                        <button
                          type="button"
                          className="small-btn"
                          disabled={!caps.canGrant}
                          title={caps.canGrant ? undefined : '이 서비스는 이 화면에서 추가할 수 없습니다.'}
                          onClick={() => onChange({ service: id, action: '부여', emails: [person.email] })}
                        >
                          {caps.grantLabel}
                        </button>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
