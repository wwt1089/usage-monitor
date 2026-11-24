import type { LogEntry } from '../domain/types'
import { SERVICE_LABEL } from '../domain/types'

const OUTCOME_CLASS: Record<LogEntry['outcome'], string> = { 성공: 'ok', 실패: 'bad', 시뮬레이션: 'info' }

export function LogPage({ entries }: { entries: LogEntry[] }) {
  return (
    <section>
      <div className="page-head">
        <h2>작업 기록</h2>
      </div>
      <p className="muted small">
        이 화면을 연 뒤에 한 작업만 보입니다. 창을 닫으면 사라지므로, 보관이 필요하면 저장소 연결(SharePoint 목록 등)을
        추가해야 합니다.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>시각</th>
              <th>작업자</th>
              <th>서비스</th>
              <th>작업</th>
              <th>대상</th>
              <th>결과</th>
              <th>내용</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, index) => (
              <tr key={`${entry.at}-${index}`}>
                <td>{new Date(entry.at).toLocaleString('ko-KR')}</td>
                <td>{entry.actor}</td>
                <td>{SERVICE_LABEL[entry.service]}</td>
                <td>{entry.action}</td>
                <td>{entry.target}</td>
                <td>
                  <span className={`badge ${OUTCOME_CLASS[entry.outcome]}`}>{entry.outcome}</span>
                </td>
                <td>{entry.message}</td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={7} className="muted center">
                  아직 작업한 내용이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
