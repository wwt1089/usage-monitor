# AI 라이선스 관리 (AI Access Manager)

Microsoft 365 Copilot · Claude · Cursor · ChatGPT 의 권한(좌석)을 한 화면에서 조회하고 부여·회수하는 웹 앱입니다.
Microsoft **Copilot Managed Runtime**(관리형 앱)에 올리는 것을 전제로, Microsoft 공식 템플릿(React + TypeScript + Vite)에서 시작했습니다.

> **현재 상태 (2026-11-24)**
> 화면과 판정 규칙은 완성되어 **예시 데이터(가상 인물)** 로 동작합니다.
> 실제 서비스 연결부는 가짜 커넥터로만 검증했고, 실제 테넌트·실제 API 키로는 아직 한 번도 호출하지 않았습니다.
> 아래 [확인되지 않은 것](#확인되지-않은-것)을 먼저 읽어 주세요.

## 화면

| 화면 | 하는 일 |
| --- | --- |
| 전체 현황 | 서비스별 좌석 사용, 최근 30일 사용자, 미사용·이력 없음·판정 불가 인원 |
| 사용자별 | 한 사람이 가진 권한을 서비스별로 보고 그 자리에서 부여·회수 (입사·퇴사·부서 이동 처리용) |
| 서비스별 4개 | 보유자 목록, 마지막 사용일, 미사용자 일괄 선택 후 회수, 이메일로 부여, 서비스별 제약 안내 |
| 작업 기록 | 이 화면에서 한 부여·회수와 결과 (창을 닫으면 사라짐) |

안전 장치

- **시뮬레이션이 기본으로 켜져 있습니다.** 켜져 있는 동안은 어떤 서비스도 바꾸지 않고 기록만 남깁니다.
- 모든 변경은 대상을 보여 주는 확인 창을 거칩니다.
- 사용 데이터가 없으면 추측하지 않고 `판정 불가`로 둡니다. 권한을 받은 지 14일이 안 된 사람은 `신규`로 보고 정리 후보에서 뺍니다.
- '미사용자 선택'은 `미사용`만 고릅니다. `사용 이력 없음`은 사람이 하나씩 확인하고 고릅니다.
- 그룹에서 상속받은 Copilot 라이선스, 관리 역할(소유자 등)은 회수 대상에서 잠급니다.

## 구조

```
src/
  domain/            공통 모델과 판정 규칙 (types, inactivity, people) + 테스트
  connectors/
    index.ts         서비스마다 실제 연결이 있으면 실제 연결, 없으면 예시 데이터를 고른다
    generated.ts     ms CLI 가 만든 generated/ 코드를 찾는 곳 (없어도 빌드됨)
    demo/            예시 데이터 연결
    live/            서비스별 실제 연결 (claude, cursor, chatgpt, m365) + 테스트
  features/          화면 (Overview, PeoplePage, ServicePage, LogPage, ConfirmDialog)
connectors/          Power Platform 사용자 지정 커넥터 정의 4개 (OpenAPI 2.0)
```

Managed Runtime 앱은 샌드박스 안에서 돌기 때문에 `fetch` 로 외부 API 를 직접 부를 수 없고, **커넥터를 통해서만** 밖과 통신합니다.
그래서 서비스마다 사용자 지정 커넥터를 만들고, API 키는 앱 코드가 아니라 **커넥터의 연결**에 저장합니다.

## 로컬에서 보기 (연결 없이)

```bash
npm install
npm run build
npx vite preview        # http://localhost:4173 — 예시 데이터로 동작
npm test                # 판정 규칙 + 실제 연결부(가짜 커넥터) 테스트
npm run lint
```

## Managed Runtime 에 올리기

준비물: Node.js 24.11 이상, Git 2.27 이상, 앱을 만들 수 있는 Power Platform 환경.
Managed Runtime 은 공개 미리 보기라 명령과 화면이 바뀔 수 있습니다.

1. **이 폴더를 다른 저장소 밖의 새 폴더로 복사합니다.** 앱마다 플랫폼이 관리하는 Git 저장소가 따로 생기므로 다른 저장소 안에 두면 안 됩니다.
2. 도구 설치와 로그인
   ```bash
   npm install -g @microsoft/cmr-cli@latest
   npm install
   ms auth login
   ```
3. 앱 등록 (`ms.config.json` 과 플랫폼 관리 저장소가 만들어집니다)
   ```bash
   ms app init --display-name "AI 라이선스 관리"
   ```
4. 여기까지만 해도 예시 데이터 화면을 올려 볼 수 있습니다.
   ```bash
   npm run build
   git add -A && git commit -m "첫 배포" && git push
   ms app deploy
   ```
5. 서비스 연결은 아래 순서로 **하나씩** 붙입니다. 커넥터를 붙이면 그 서비스만 `실제 연결`로 바뀝니다.

### 서비스 연결

1. [Power Apps](https://make.powerapps.com) > 앱과 같은 환경 > **사용자 지정 커넥터 > 새로 만들기 > OpenAPI 파일 가져오기** 로 `connectors/<서비스>/apiDefinition.swagger.json` 을 올립니다.
2. 보안 탭을 아래 표대로 설정하고 커넥터를 만든 뒤, **연결**을 만들면서 키를 입력합니다.
3. 앱 폴더에서 커넥터를 추가합니다. `<api-id>` 는 `ms connector list --search "<이름>"` 으로 찾습니다.
   ```bash
   ms app add data-source --connector <api-id> --as action
   npm run build
   ```
4. `ms app dev` 로 로컬에서 확인한 뒤 커밋·푸시하고 `ms app deploy` 합니다.

| 서비스 | 커넥터 정의 | 보안 설정 | 연결에 넣는 값 | 키를 만드는 사람 |
| --- | --- | --- | --- | --- |
| Claude | `connectors/claude-admin` | API 키, 헤더 `x-api-key`. **정의 탭 > 정책 > 헤더 설정**으로 `anthropic-version` 헤더 추가(값은 Anthropic 문서의 현재 버전) | claude.ai 조직 설정 > API 에서 만든 키. 범위 `read:members`, `write:members`, `read:analytics` | Primary owner |
| Cursor | `connectors/cursor-admin` | 기본 인증(Basic) | 사용자 이름 = Admin API 키, 비밀번호 = 비움 | 팀 관리자 |
| ChatGPT | `connectors/chatgpt-admin` | API 키, 헤더 `Authorization` | `Bearer <워크스페이스 Admin 키>` (앞의 `Bearer ` 포함) | 워크스페이스 소유자·관리자 (Enterprise·Edu) |
| Microsoft 365 | `connectors/m365-graph` | OAuth 2.0 > Azure Active Directory. Entra 앱 등록의 클라이언트 ID·비밀, 리소스 URL `https://graph.microsoft.com` | 관리자가 자기 계정으로 로그인 | 앱 등록: Entra 관리자 |

Microsoft 365 커넥터의 Entra 앱 등록에 필요한 위임 권한: `User.Read.All`, `LicenseAssignment.ReadWrite.All`, `Reports.Read.All`.
화면을 쓰는 관리자의 Entra 역할(License Administrator, Reports Reader 등)이 그대로 적용됩니다.

설정값 (`.env.example` → `.env.local`)

| 이름 | 용도 |
| --- | --- |
| `VITE_CHATGPT_WORKSPACE_ID` | ChatGPT 워크스페이스 ID |
| `VITE_M365_GRANT_SKU` | Copilot SKU 가 여러 개일 때 부여에 쓸 SKU 이름 |

### 서비스별로 이 화면에서 되는 것

| 서비스 | 조회 | 마지막 사용일 | 부여 | 회수 |
| --- | --- | --- | --- | --- |
| Microsoft 365 Copilot | ✅ | ✅ 사용 리포트 | ✅ 직접 할당 | ✅ 직접 할당만 (그룹 상속은 잠금) |
| Claude | ✅ | ✅ 최근 90일 | ✅ 초대 | ✅ 제거·초대 취소 (SCIM·JIT 조직은 API 가 거절) |
| Cursor | ✅ | ✅ 최근 90일 | ❌ API 없음 (대시보드·SCIM) | ✅ Enterprise 플랜만 |
| ChatGPT | ✅ | ❓ 응답에 있으면 표시 | ❌ 아직 연결 안 함 | ❌ 아직 연결 안 함 |

## 확인되지 않은 것

- **Managed Runtime 에서 사용자 지정 커넥터가 그대로 쓰이는지.** Microsoft 문서는 커넥터가 없을 때의 대안으로 사용자 지정 커넥터를 들지만, 이 앱으로 실제 환경에서 해 보지는 않았습니다. 조직 정책이 커넥터나 동작을 막을 수도 있습니다(`ms connector list` 로 확인).
- **생성 코드의 인자 순서.** `src/connectors/live/*.ts` 는 커넥터 정의의 매개변수 순서대로 값을 넘깁니다. `ms app add data-source` 뒤에 `generated/services/*.ts` 의 실제 시그니처와 한 번 대조해야 합니다.
- **Cursor 응답 필드 이름.** 공식 문서를 직접 열지 못해 주소만 확인했고, 응답 필드는 여러 후보(`teamMembers`, `data` 등)를 함께 읽도록 했습니다. Basic 인증에서 빈 비밀번호가 허용되는지도 확인이 필요합니다.
- **ChatGPT.** 확인된 주소는 사용자 목록 하나입니다. 초대·제거·사용량은 Admin API reference 로 주소와 응답을 확인한 뒤 추가해야 합니다.
- **Claude 의 `anthropic-version` 값**, **Graph `/beta` 사용 리포트의 응답 형식**(Copilot Manager 에서 쓰던 주소와 같음)도 실제 호출로 확인해야 합니다.
- 작업 기록은 브라우저 메모리에만 있습니다. 감사 기록으로 남기려면 SharePoint 목록이나 Dataverse 연결을 추가해야 합니다.
- 화면에 들어온 사람은 누구나 연결된 키의 권한으로 작업할 수 있습니다. **앱 공유 대상을 관리자 그룹으로 제한**해야 합니다(`ms app share`).
