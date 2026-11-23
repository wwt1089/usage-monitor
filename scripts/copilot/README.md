# Copilot Manager

Microsoft 365 Copilot 라이선스를 **조회 · 부여 · 회수**하고, 일정 기간 쓰지 않은 사용자를 찾아 **확인 후 회수**하는 PowerShell 도구입니다. 화면(GUI)과 명령(CLI) 두 방식으로 쓸 수 있고, 이 PC의 Windows Copilot 차단/해제도 함께 다룹니다.

> 모든 변경 작업은 **시뮬레이션이 기본**입니다. 화면은 하단 체크박스, 명령은 `-WhatIf`로 먼저 결과를 확인한 뒤 실제 적용하세요.

---

## 목차
1. [구성 파일](#1-구성-파일)
2. [사전 준비 (최초 1회)](#2-사전-준비-최초-1회)
3. [작업별 필요 권한](#3-작업별-필요-권한)
4. [빠른 시작](#4-빠른-시작)
5. [미사용자 정리 동작 방식](#5-미사용자-정리-동작-방식)
6. [안전장치](#6-안전장치)
7. [로그와 산출물](#7-로그와-산출물)
8. [문제 해결](#8-문제-해결)
9. [커밋 메시지 규칙](#9-커밋-메시지-규칙)
10. [참고 문서](#10-참고-문서)

---

## 1. 구성 파일

| 파일 | 설명 |
|---|---|
| `Copilot-Manager.cmd` | 더블클릭으로 화면 실행 |
| `Copilot-Manager-GUI.ps1` | WPF 관리 화면 (`Manage-Copilot.ps1`의 기능을 불러와 사용) |
| `Manage-Copilot.ps1` | 핵심 기능. `-Action`으로 명령 실행, 인자 없이 dot-source 하면 함수만 로드 |
| `users.sample.csv` | 부여/회수/동기화용 CSV 예시 (`UserPrincipalName` 열 필수) |

---

## 2. 사전 준비 (최초 1회)

### 2-1. 실행 환경
- Windows 10/11, Windows PowerShell 5.1 또는 PowerShell 7 이상
- 화면(GUI)은 Windows 전용입니다.

### 2-2. Microsoft Graph PowerShell 모듈
```powershell
Install-Module Microsoft.Graph -Scope CurrentUser
```
필요 하위 모듈: `Microsoft.Graph.Authentication`, `Microsoft.Graph.Users`, `Microsoft.Graph.Groups`, `Microsoft.Graph.Identity.DirectoryManagement`

### 2-3. Graph 권한 관리자 동의
처음 연결할 때 `User.Read.All`, `Reports.Read.All` 같은 `*.All` 위임 권한은 **관리자 동의**가 필요합니다. 동의 창이 뜨면 Global Administrator(또는 Privileged Role Administrator / Cloud Application Administrator)가 한 번 승인해야 합니다. 승인되지 않으면 스크립트가 `Graph 연결에 필요한 권한이 승인되지 않았습니다.`로 중단됩니다.

### 2-4. ⚠ 조직 설정: 보고서 사용자 이름 숨김 해제 (미사용자 조회 시 필수)

Microsoft 365는 2021-09-01부터 사용 현황 보고서의 사용자 이름을 **기본으로 숨김(익명화)** 처리합니다. 이 상태에서는 Copilot 사용 리포트의 UPN이 해시값으로 나와 실제 사용자와 대조할 수 없으므로, 스크립트가 **미사용자 조회를 중단**합니다 (모든 사용자를 미사용으로 잘못 판정하는 것을 막기 위한 안전장치).

| 항목 | 내용 |
|---|---|
| 위치 | Microsoft 365 관리 센터 → **설정 → 조직 설정 → 서비스 → 보고서** |
| 변경 | **"모든 보고서에서 숨겨진 사용자, 그룹 및 사이트 이름 표시"** 체크 (영문: *Display concealed user, group, and site names in all reports*) |
| 필요 역할 | **Global Administrator** |
| 영향 범위 | 테넌트 전체 사용 보고서 (Teams 관리 센터 보고서 포함) |
| 반영 시간 | 변경 후 리포트 반영까지 시간이 걸릴 수 있음 |

- **이미 해제되어 있는 조직이면 따로 할 일이 없습니다.**
- 개인정보 정책상 해제가 어렵다면 미사용자 정리 기능은 쓸 수 없고, 나머지 기능(현황/부여/회수/그룹/Windows)은 영향이 없습니다.
- 메뉴 이름은 관리 센터 언어·버전에 따라 조금 다를 수 있습니다.

---

## 3. 작업별 필요 권한

스크립트는 **작업마다 필요한 Graph 권한만** 요청합니다. 시뮬레이션 모드에서는 쓰기 권한을 요청하지 않습니다. 아래 "Entra 역할"은 그 작업을 수행하는 계정에 필요한 **최소 관리자 역할**입니다 (Global Administrator는 모두 가능하지만 상시 사용은 권장하지 않음).

### 3-1. 작업별 표

| 작업 | 화면 위치 | CLI `-Action` | 최소 Entra 역할 | 스크립트가 요청하는 Graph 위임 권한 | 비고 |
|---|---|---|---|---|---|
| 라이선스 현황 | 라이선스 현황 → 현황 새로고침 | `Status` | License Administrator 또는 Global Reader | `LicenseAssignment.Read.All` | |
| 보유자 목록 | 라이선스 현황 → 보유자 목록 조회 | `List` | License Administrator 또는 Global Reader | `LicenseAssignment.Read.All`, `User.Read.All` | |
| 사용자 부여 | 부여/회수 → 라이선스 부여 | `Grant` | **License Administrator** | 위 + `LicenseAssignment.ReadWrite.All`, `User.ReadWrite.All` | 사용 위치(UsageLocation)가 비어 있으면 자동 설정 |
| 사용자 회수 | 부여/회수 → 라이선스 회수 | `Revoke` | **License Administrator** | `LicenseAssignment.Read.All`, `User.Read.All`, `LicenseAssignment.ReadWrite.All` | **직접 할당분만** 회수 |
| CSV 동기화 | (CLI 전용) | `Sync` | **License Administrator** | 부여 + 회수 권한 | |
| 그룹 라이선스 부여/회수 | 부여/회수 → 그룹에 부여 / 그룹에서 회수 | `GrantGroup` / `RevokeGroup` | **License Administrator** | `LicenseAssignment.Read.All`, `GroupMember.Read.All`, `LicenseAssignment.ReadWrite.All` | 보안 그룹만 가능 |
| 미사용자 조회 | 미사용자 정리 → ① 조회 | `Inactive` | **Reports Reader** + 목록 조회 권한(License Administrator 또는 Global Reader) | `LicenseAssignment.Read.All`, `User.Read.All`, `Reports.Read.All` | **2-4 조직 설정 필수** |
| 미사용자 회수 | 미사용자 정리 → ② 직접 할당 회수 | `Inactive` 후 A/S 선택 | Reports Reader + **License Administrator** | 조회 권한 + `LicenseAssignment.ReadWrite.All` | 화면은 회수 직전 최신 리포트로 재확인 |
| 그룹 멤버십 제거 | 미사용자 정리 → 선택 사용자 그룹에서 제외 | `Revoke`/`Inactive` + `-RemoveFromGroup` | **Groups Administrator** (또는 User Administrator) | `GroupMember.Read.All`, `GroupMember.ReadWrite.All` | 동적 그룹 제외. 역할 할당 가능 그룹은 Privileged Role Administrator 필요. 대화형 실행에서만 가능 |
| Windows Copilot 상태 | Windows Copilot → 상태 조회 | `WinStatus` | (Entra 역할 불필요) | 없음 | |
| Windows Copilot 차단/해제 – PC 전체 | Windows Copilot → 이 PC 전체 | `WinDisable` / `WinEnable` `-Scope Machine` | **로컬 관리자 권한** PowerShell | 없음 | 모든 사용자 앱 제거 + 프로비저닝 제거 |
| Windows Copilot 차단/해제 – 현재 사용자 | Windows Copilot → 현재 사용자 | `WinDisable` / `WinEnable` `-Scope User` | 일반 사용자 | 없음 | 현재 사용자 앱만 제거 |

### 3-2. 담당자별 권장 역할 조합

| 담당 | 권장 역할 | 가능한 작업 |
|---|---|---|
| 현황 보고 담당 | Global Reader + Reports Reader | 현황, 보유자 목록, 미사용자 조회 (변경 불가) |
| 라이선스 운영 담당 | License Administrator + Reports Reader | 위 + 부여/회수/동기화/그룹 라이선스/미사용자 회수 |
| 그룹 정리까지 담당 | 위 + Groups Administrator | 위 + 그룹 상속 사용자 멤버십 제거 |
| 조직 설정 변경 (1회) | Global Administrator | 2-3 관리자 동의, 2-4 보고서 이름 숨김 해제 |

> PIM(Privileged Identity Management)을 쓰는 조직은 작업할 때만 역할을 활성화하는 방식을 권장합니다.

---

## 4. 빠른 시작

### 4-1. 화면(GUI)
1. `Copilot-Manager.cmd` 더블클릭 (Windows Copilot PC 전체 제어 시 **관리자 권한으로 실행**)
2. 상단 **Graph 연결** → 관리자 계정 로그인
3. **라이선스 현황 → 현황 새로고침**으로 SKU 이름 확인 (`Microsoft_365_Copilot`이 아니면 상단 SKU 칸 수정)
4. **미사용자 정리 → ① 조회** → 결과 확인 → 회수할 사용자 체크 → **② 직접 할당 회수**
5. 시뮬레이션 결과가 맞으면 하단 **시뮬레이션 모드** 체크 해제 후 다시 실행

### 4-2. 명령(CLI)
```powershell
# 현황 / 목록
.\Manage-Copilot.ps1 -Action Status
.\Manage-Copilot.ps1 -Action List

# 부여 / 회수 (먼저 -WhatIf)
.\Manage-Copilot.ps1 -Action Grant  -User kim@contoso.com,lee@contoso.com -UsageLocation KR -WhatIf
.\Manage-Copilot.ps1 -Action Revoke -CsvPath .\users.csv -WhatIf

# 그룹 라이선스 (권장 운영 방식)
.\Manage-Copilot.ps1 -Action GrantGroup -GroupName "SG-Copilot-Users"

# CSV 허용 목록 기준 동기화
.\Manage-Copilot.ps1 -Action Sync -CsvPath .\allowlist.csv -WhatIf

# 30일 미사용자 조회 → [A] 전체 / [S] 선택 / [N] 조회만
.\Manage-Copilot.ps1 -Action Inactive -InactiveDays 30 -Exclude ceo@contoso.com

# 작업 스케줄러용 자동 회수 (직접 할당분만, 반드시 -WhatIf로 먼저 검증)
.\Manage-Copilot.ps1 -Action Inactive -InactiveDays 30 -AutoRevoke -WhatIf

# Windows Copilot
.\Manage-Copilot.ps1 -Action WinStatus
.\Manage-Copilot.ps1 -Action WinDisable -Scope Machine   # 관리자 PowerShell
```

### 4-3. 주요 매개변수

| 매개변수 | 기본값 | 설명 |
|---|---|---|
| `-SkuPartNumber` | `Microsoft_365_Copilot` | 테넌트의 Copilot SKU 이름. 못 찾으면 후보 목록을 안내 |
| `-UsageLocation` | `KR` | 부여 시 사용 위치가 비어 있는 사용자에게 설정할 국가 코드 |
| `-InactiveDays` | `30` | 미사용 판정 기준 일수 (리포트 제공 범위 최대 180일) |
| `-Exclude` | 없음 | 회수 대상에서 항상 제외할 UPN |
| `-AutoRevoke` | 꺼짐 | 질문 없이 후보 전원 직접 할당 회수 (그룹 멤버십 제거와 함께 쓸 수 없음) |
| `-RemoveFromGroup` | 꺼짐 | 그룹 상속 사용자를 대상 확인 후 그룹에서 제외 (대화형 전용) |
| `-Scope` | `Machine` | Windows 정책 범위: `Machine`(HKLM) / `User`(HKCU) |
| `-WhatIf` | | 실제 변경 없이 시뮬레이션 |

---

## 5. 미사용자 정리 동작 방식

1. Copilot 라이선스 보유자 목록과 **Copilot 사용 리포트(최근 180일, 실패 시 90일)**를 가져옵니다.
2. 리포트 기준일(reportRefreshDate)에서 `-InactiveDays`를 뺀 날짜보다 마지막 사용일이 오래되면 미사용으로 판정합니다. 리포트는 보통 2~3일 늦게 집계되므로 오늘 날짜가 아닌 리포트 기준일을 씁니다.
3. 결과를 먼저 보여주고 CSV로 저장한 뒤, 회수 여부는 사람이 결정합니다.
4. (화면) 회수 직전에 **최신 리포트로 다시 판정**해, 그사이 사용을 시작한 사용자는 건너뜁니다.

| 판정 | 기준 | 회수 후보 |
|---|---|---|
| 미사용 | 마지막 사용일 ≤ 기준일 − N일 | ✅ |
| 사용이력없음 | 리포트에 있으나 사용 기록 없음 (라이선스 받은 지 N일 경과) | ✅ |
| 사용중 | 기준 기간 내 사용 기록 있음 | ❌ |
| 신규-유예 | 라이선스 받은 지 N일 미만 | ❌ |
| 제외대상 | `-Exclude` / 화면 제외 목록 | ❌ |
| 판정 불가 | 리포트에 사용자가 없거나 라이선스 시작일도 확인 불가 | ❌ (수동 확인) |

**할당 방식**: `Direct`(직접) / `Group`(그룹 상속) / `Mixed`(둘 다)
- 회수는 **직접 할당분만** 제거합니다. `Mixed`는 회수 후에도 그룹 라이선스가 남습니다.
- `Group`/`Mixed` 사용자의 권한을 완전히 없애려면 그룹 멤버십 제거(별도 확인 단계)가 필요합니다. 그룹에서 빼면 **그 그룹에 걸린 다른 라이선스·접근 권한도 함께 빠질 수 있으니** 확인 창의 그룹 목록을 꼭 검토하세요.

---

## 6. 안전장치
- **시뮬레이션 기본값**: 화면은 시뮬레이션 체크가 기본, 해제 시 경고 확인
- **최소 권한 연결**: 작업별 필요한 Graph 권한만 요청, 시뮬레이션 시 쓰기 권한 미요청
- **계정/테넌트 변경 감지**: 연결 중 계정이나 테넌트가 바뀌면 작업 중단, (화면) 조회 후 SKU/연결이 바뀌면 재조회 요구
- **익명화 리포트 감지**: 사용자 이름이 숨겨진 리포트면 조회 중단 (2-4 참고)
- **빈 리포트 감지**: 보유자는 있는데 리포트가 비면 판정 중단
- **잔여 수량 확인**: 잔여 라이선스가 없으면 부여 중단
- **그룹 제거 분리**: 라이선스 회수와 그룹 멤버십 제거를 별도 단계·별도 확인으로 처리, 자동 실행에서는 불가

---

## 7. 로그와 산출물
스크립트와 같은 폴더에 생성됩니다 (시뮬레이션 중에는 변경 로그를 남기지 않음).

| 파일 | 내용 |
|---|---|
| `copilot-log-yyyyMMdd.csv` | 변경 작업 로그 (시간, 실행자, 작업, 대상, 결과, 상세) |
| `copilot-inactive-yyyyMMdd.csv` | CLI 미사용자 조회 결과 (화면에서는 CSV 저장 버튼으로 위치 선택) |

---

## 8. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| `리포트 사용자명이 익명화되어 있습니다` | 2-4 조직 설정 해제 필요 (Global Administrator) |
| `사용 리포트가 비어 있습니다` | 리포트 집계 지연 또는 신규 라이선스. 1~2일 후 재시도 |
| `Copilot 사용 리포트를 가져오지 못했습니다` | Reports Reader 역할과 `Reports.Read.All` 동의 확인 |
| `Graph 연결에 필요한 권한이 승인되지 않았습니다` | 2-3 관리자 동의 필요 |
| `Graph 계정 또는 테넌트가 달라 작업을 중단했습니다` | 다른 계정으로 로그인됨. 같은 계정으로 다시 연결 |
| `SKU '...' 를 찾지 못했습니다` | 안내된 후보 중 하나를 `-SkuPartNumber` / 화면 SKU 칸에 입력 |
| `사용 리포트는 최근 180일까지 제공됩니다` | `-InactiveDays`를 180 이하로 |
| 회수했는데 Copilot이 계속 보임 | `Group`/`Mixed` 사용자 → 그룹 멤버십 제거 필요. 클라이언트 반영에 시간 소요 |
| 그룹 제외가 `SKIP 동적 그룹` | 동적 그룹은 멤버십 규칙을 수정해야 함 |
| `PC 전체 범위는 관리자 권한 PowerShell에서 실행하세요` | 관리자 권한으로 실행하거나 `-Scope User` 사용 |
| 한글이 깨짐 | 파일이 UTF-8 **BOM 포함**으로 저장되어 있어야 함 (Windows PowerShell 5.1) |

---

## 9. 커밋 메시지 규칙
변경 이력을 나중에 읽기 쉽게 아래 형식을 권장합니다.

```
<영역>: <무엇을 왜 바꿨는지 한 줄 요약>

- 상세 변경 1
- 상세 변경 2
```

예시
- `docs: README에 작업별 필요 관리자 역할과 보고서 이름 숨김 해제 절차 추가`
- `inactive: 회수 직전 최신 리포트로 후보 재확인`
- `gui: 그룹 멤버십 제거를 별도 확인 창으로 분리`

> `Import ... from usage-monitor 2dce3b7`처럼 내부 해시만 있는 메시지는 피하고, 다른 저장소에서 가져온 경우 본문에 출처를 적어주세요.
> 예: `chore: usage-monitor에서 Copilot Manager 분리` + 본문 `출처: wwt1089/usage-monitor@2dce3b7 (scripts/copilot)`

---

## 10. 참고 문서
- [Copilot 사용 리포트 API (Graph beta)](https://learn.microsoft.com/en-us/graph/api/reportroot-getmicrosoft365copilotusageuserdetail?view=graph-rest-beta)
- [Copilot 사용 리포트 API (v1.0, copilot/reports)](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/reports/copilotreportroot-getmicrosoft365copilotusageuserdetail)
- [사용 보고서 API 권한 요구사항](https://learn.microsoft.com/en-us/graph/reportroot-authorization)
- [보고서에 익명 사용자 이름이 표시되는 경우](https://learn.microsoft.com/en-us/troubleshoot/microsoft-365/admin/miscellaneous/reports-show-anonymous-user-name)
- [Microsoft Entra 기본 제공 역할](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference)
