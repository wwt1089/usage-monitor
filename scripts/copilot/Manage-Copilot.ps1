<#
.SYNOPSIS
    Microsoft Copilot 권한 부여 / 회수 / 제어 통합 스크립트

.DESCRIPTION
    [M365 Copilot 라이선스 - Microsoft Graph]
      Status       : 테넌트 Copilot 라이선스 보유/사용/잔여 수량
      List         : Copilot 라이선스 보유 사용자 목록 (직접/그룹 할당 구분)
      Grant        : 사용자에게 Copilot 라이선스 부여
      Revoke       : 사용자 Copilot 라이선스 회수
      GrantGroup   : 보안 그룹에 그룹 기반 라이선스 부여 (권장 운영 방식)
      RevokeGroup  : 보안 그룹 라이선스 회수
      Sync         : CSV(허용 목록) 기준으로 부여/회수 자동 동기화
      Inactive     : N일 이상 Copilot 미사용자 '조회 우선' → 결과 확인 후 회수 여부 선택
                     ([A] 전체 회수 / [S] 선택 회수 / [N] 조회만). 리포트 CSV 자동 저장

    [Windows 로컬 Copilot 제어 - 관리자 권한 필요]
      WinDisable   : Windows Copilot 정책 차단 + Copilot 앱 제거 + Edge 사이드바 차단
      WinEnable    : 위 정책 해제
      WinStatus    : 현재 로컬 정책/앱 상태 조회

    모든 변경 작업은 -WhatIf 로 사전 시뮬레이션 가능하며, 결과는 CSV 로그로 남습니다.

.EXAMPLE
    .\Manage-Copilot.ps1 -Action Inactive -InactiveDays 30                 # 조회 후 회수 여부 질문
    .\Manage-Copilot.ps1 -Action Inactive -InactiveDays 60 -Exclude ceo@contoso.com
    .\Manage-Copilot.ps1 -Action Inactive -InactiveDays 30 -AutoRevoke -WhatIf   # 스케줄 실행용 시뮬레이션
    .\Manage-Copilot.ps1 -Action Status
    .\Manage-Copilot.ps1 -Action Grant  -User kim@contoso.com,lee@contoso.com -UsageLocation KR
    .\Manage-Copilot.ps1 -Action Grant  -CsvPath .\users.csv -WhatIf
    .\Manage-Copilot.ps1 -Action Revoke -User kim@contoso.com
    .\Manage-Copilot.ps1 -Action GrantGroup -GroupName "SG-Copilot-Users"
    .\Manage-Copilot.ps1 -Action Sync   -CsvPath .\allowlist.csv -WhatIf
    .\Manage-Copilot.ps1 -Action WinDisable -Scope Machine

.NOTES
    필요 모듈 : Microsoft.Graph.Authentication, Microsoft.Graph.Users,
                Microsoft.Graph.Groups, Microsoft.Graph.Identity.DirectoryManagement
                (Install-Module Microsoft.Graph -Scope CurrentUser)
    필요 역할 : License Administrator 또는 User Administrator (그룹 라이선스는 Groups Administrator 이상)
    CSV 형식  : 헤더에 UserPrincipalName 컬럼 필수
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    # 생략 시: 함수만 로드 (GUI 에서 dot-source 용)
    [ValidateSet('Status', 'List', 'Grant', 'Revoke', 'GrantGroup', 'RevokeGroup', 'Sync', 'Inactive',
                 'WinDisable', 'WinEnable', 'WinStatus')]
    [string]$Action,

    [string[]]$User,
    [string]$CsvPath,
    [string]$GroupName,

    # Copilot SKU 파트넘버. 테넌트마다 다를 수 있으니 Status 결과로 확인 후 지정
    [string]$SkuPartNumber = 'Microsoft_365_Copilot',

    # 라이선스 부여 시 사용 위치가 비어 있는 사용자에게 설정할 국가 코드
    [string]$UsageLocation = 'KR',

    # Windows 정책 적용 범위: User = 현재 사용자(HKCU), Machine = 전체 PC(HKLM)
    [ValidateSet('User', 'Machine')]
    [string]$Scope = 'Machine',

    [string]$LogPath = (Join-Path $PSScriptRoot ("copilot-log-{0:yyyyMMdd}.csv" -f (Get-Date))),

    # ── Inactive(미사용자 정리) 옵션 ──
    # 이 일수 이상 Copilot 활동이 없으면 미사용자로 판정 (최대 180일까지 판정 가능)
    [ValidateRange(1, 365)]
    [int]$InactiveDays = 30,

    # 회수 대상에서 항상 제외할 사용자(UPN) - 임원, 파일럿 담당자 등
    [string[]]$Exclude,

    # 질문 없이 미사용자 전원 회수 (작업 스케줄러용). -WhatIf 와 함께 먼저 검증할 것
    [switch]$AutoRevoke,

    # 그룹 기반 라이선스 사용자는 '라이선스 그룹에서 제외'하는 방식으로 회수
    [switch]$RemoveFromGroup,

    [string]$ReportPath = (Join-Path $PSScriptRoot ("copilot-inactive-{0:yyyyMMdd}.csv" -f (Get-Date)))
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# GUI 연동용 상태 (StrictMode 에서 미정의 변수 오류 방지)
if (-not (Test-Path variable:script:GuiMode))  { $script:GuiMode  = $false }
if (-not (Test-Path variable:script:GuiWhatIf)) { $script:GuiWhatIf = $true }
if (-not (Test-Path variable:script:LogSink))  { $script:LogSink  = $null }

#region ── 공통 유틸 ───────────────────────────────────────────────
function Write-Log {
    param([string]$Target, [string]$Operation, [string]$Result, [string]$Detail = '')
    $row = [pscustomobject]@{
        Time      = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        Operator  = $env:USERNAME
        Operation = $Operation
        Target    = $Target
        Result    = $Result
        Detail    = $Detail
    }
    $color = switch ($Result) { 'OK' { 'Green' } 'SKIP' { 'Yellow' } 'WHATIF' { 'Cyan' } 'INFO' { 'Gray' } default { 'Red' } }
    $line = "[{0}] {1,-12} {2,-40} {3}" -f $Result, $Operation, $Target, $Detail
    if ($script:LogSink) { & $script:LogSink $line $Result } else { Write-Host $line -ForegroundColor $color }
    if (-not (Test-Simulation)) {
        $row | Export-Csv -Path $LogPath -Append -NoTypeInformation -Encoding UTF8
    }
}

# 시뮬레이션 여부: GUI 모드면 GUI 체크박스, 아니면 -WhatIf
function Test-Simulation {
    if ($script:GuiMode) { return [bool]$script:GuiWhatIf }
    return [bool]$WhatIfPreference
}

# 변경 실행 여부: GUI 모드면 시뮬레이션 체크로, CLI 면 ShouldProcess(-WhatIf/-Confirm)로 판단
function Confirm-Change {
    param([string]$Target, [string]$Operation)
    if ($script:GuiMode) { return -not $script:GuiWhatIf }
    return $PSCmdlet.ShouldProcess($Target, $Operation)
}

function Get-TargetUsers {
    $list = @()
    if ($User)    { $list += $User }
    if ($CsvPath) {
        if (-not (Test-Path $CsvPath)) { throw "CSV 파일을 찾을 수 없습니다: $CsvPath" }
        $csv = Import-Csv $CsvPath
        if (-not ($csv | Get-Member -Name UserPrincipalName)) {
            throw "CSV 에 'UserPrincipalName' 컬럼이 필요합니다."
        }
        $list += $csv.UserPrincipalName
    }
    $list = $list | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim().ToLower() } | Sort-Object -Unique
    if (-not $list) { throw "-User 또는 -CsvPath 로 대상 사용자를 지정하세요." }
    return $list
}

function Assert-Admin {
    param([switch]$Always)
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin -and ($Always -or $Scope -eq 'Machine')) {
        throw "관리자 권한 PowerShell 에서 실행하세요. (Scope=Machine 또는 앱 제거 작업)"
    }
}
#endregion

#region ── Microsoft Graph (M365 Copilot 라이선스) ────────────────
function Connect-CopilotGraph {
    $modules = 'Microsoft.Graph.Authentication', 'Microsoft.Graph.Users',
               'Microsoft.Graph.Groups', 'Microsoft.Graph.Identity.DirectoryManagement'
    foreach ($m in $modules) {
        if (-not (Get-Module -ListAvailable -Name $m)) {
            throw "모듈 '$m' 이 없습니다. 먼저 실행: Install-Module Microsoft.Graph -Scope CurrentUser"
        }
    }
    $scopes = 'User.ReadWrite.All', 'Group.ReadWrite.All', 'Organization.Read.All', 'Directory.Read.All',
              'Reports.Read.All'
    $ctx = Get-MgContext
    $missing = if ($ctx) { $scopes | Where-Object { $_ -notin $ctx.Scopes } } else { $scopes }
    if (-not $ctx -or $missing) {
        Connect-MgGraph -Scopes $scopes -NoWelcome
    }
    Write-Host "Graph 연결: $((Get-MgContext).Account)" -ForegroundColor DarkGray
}

function Get-CopilotSku {
    $skus = Get-MgSubscribedSku -All
    $sku  = $skus | Where-Object SkuPartNumber -eq $SkuPartNumber
    if (-not $sku) {
        # 이름에 Copilot 이 포함된 SKU 후보를 안내
        $cands = $skus | Where-Object SkuPartNumber -match 'Copilot' | Select-Object -ExpandProperty SkuPartNumber
        throw "SKU '$SkuPartNumber' 를 찾지 못했습니다. 후보: $($cands -join ', ')  →  -SkuPartNumber 로 지정하세요."
    }
    return $sku
}

function Get-CopilotStatus {
    $sku = Get-CopilotSku
    $total = $sku.PrepaidUnits.Enabled
    [pscustomobject]@{
        SkuPartNumber = $sku.SkuPartNumber
        SkuId         = $sku.SkuId
        Purchased     = $total
        Assigned      = $sku.ConsumedUnits
        Available     = $total - $sku.ConsumedUnits
        Suspended     = $sku.PrepaidUnits.Suspended
        Warning       = $sku.PrepaidUnits.Warning
    }
}

function Show-Status { Get-CopilotStatus | Format-List }

function Get-CopilotUsers {
    $sku = Get-CopilotSku
    $users = Get-MgUser -All -ConsistencyLevel eventual -CountVariable cnt `
        -Filter "assignedLicenses/any(x:x/skuId eq $($sku.SkuId))" `
        -Property Id, DisplayName, UserPrincipalName, Department, AccountEnabled, LicenseAssignmentStates

    foreach ($u in $users) {
        $states = @($u.LicenseAssignmentStates | Where-Object SkuId -eq $sku.SkuId)
        [pscustomobject]@{
            UserPrincipalName = $u.UserPrincipalName
            DisplayName       = $u.DisplayName
            Department        = $u.Department
            Enabled           = $u.AccountEnabled
            AssignedBy        = if ($states | Where-Object { $_.AssignedByGroup }) { 'Group' } else { 'Direct' }
            State             = ($states.State | Sort-Object -Unique) -join ','
        }
    }
}

function Grant-CopilotLicense {
    param([string[]]$Upns)
    $sku = Get-CopilotSku
    $available = $sku.PrepaidUnits.Enabled - $sku.ConsumedUnits
    Write-Host "잔여 라이선스: $available / 요청: $($Upns.Count)" -ForegroundColor DarkGray

    foreach ($upn in $Upns) {
        try {
            $u = Get-MgUser -UserId $upn -Property Id, UserPrincipalName, UsageLocation, AssignedLicenses
            if ($u.AssignedLicenses.SkuId -contains $sku.SkuId) {
                Write-Log $upn 'Grant' 'SKIP' '이미 보유'; continue
            }
            if ($available -le 0) {
                Write-Log $upn 'Grant' 'FAIL' '잔여 라이선스 없음'; continue
            }
            if ((Confirm-Change -Target $upn -Operation "Copilot 라이선스 부여")) {
                if (-not $u.UsageLocation) {
                    Update-MgUser -UserId $u.Id -UsageLocation $UsageLocation
                }
                Set-MgUserLicense -UserId $u.Id -AddLicenses @(@{ SkuId = $sku.SkuId }) -RemoveLicenses @() | Out-Null
                $available--
                Write-Log $upn 'Grant' 'OK'
            } else {
                Write-Log $upn 'Grant' 'WHATIF'
            }
        } catch {
            Write-Log $upn 'Grant' 'FAIL' $_.Exception.Message
        }
    }
}

function Revoke-CopilotLicense {
    param([string[]]$Upns)
    $sku = Get-CopilotSku
    foreach ($upn in $Upns) {
        try {
            $u = Get-MgUser -UserId $upn -Property Id, AssignedLicenses, LicenseAssignmentStates
            if ($u.AssignedLicenses.SkuId -notcontains $sku.SkuId) {
                Write-Log $upn 'Revoke' 'SKIP' '미보유'; continue
            }
            $byGroup = $u.LicenseAssignmentStates | Where-Object { $_.SkuId -eq $sku.SkuId -and $_.AssignedByGroup }
            $direct  = $u.LicenseAssignmentStates | Where-Object { $_.SkuId -eq $sku.SkuId -and -not $_.AssignedByGroup }
            if ($byGroup -and -not $direct) {
                # 그룹 상속 라이선스는 사용자 단위로 회수 불가 → 라이선스 그룹에서 제외해야 함
                foreach ($g in $byGroup) {
                    $grp = Get-MgGroup -GroupId $g.AssignedByGroup -Property Id, DisplayName, GroupTypes
                    if (-not $RemoveFromGroup) {
                        Write-Log $upn 'Revoke' 'SKIP' "그룹 상속($($grp.DisplayName)) - -RemoveFromGroup 지정 시 그룹에서 제외"
                        continue
                    }
                    if ($grp.GroupTypes -contains 'DynamicMembership') {
                        Write-Log $upn 'Revoke' 'FAIL' "동적 그룹($($grp.DisplayName)) - 멤버십 규칙을 수정해야 함"
                        continue
                    }
                    if ((Confirm-Change -Target "$upn ← $($grp.DisplayName)" -Operation "라이선스 그룹에서 제외")) {
                        Remove-MgGroupMemberDirectoryObjectByRef -GroupId $grp.Id -DirectoryObjectId $u.Id
                        Write-Log $upn 'Revoke' 'OK' "그룹에서 제외: $($grp.DisplayName)"
                    } else {
                        Write-Log $upn 'Revoke' 'WHATIF' "그룹에서 제외: $($grp.DisplayName)"
                    }
                }
                continue
            }
            if ($byGroup) {
                Write-Log $upn 'Revoke' 'INFO' '직접 할당분만 회수됨 - 그룹 상속 라이선스는 유지'
            }
            if ((Confirm-Change -Target $upn -Operation "Copilot 라이선스 회수")) {
                Set-MgUserLicense -UserId $u.Id -AddLicenses @() -RemoveLicenses @($sku.SkuId) | Out-Null
                Write-Log $upn 'Revoke' 'OK'
            } else {
                Write-Log $upn 'Revoke' 'WHATIF'
            }
        } catch {
            Write-Log $upn 'Revoke' 'FAIL' $_.Exception.Message
        }
    }
}

function Set-CopilotGroupLicense {
    param([string]$Name, [switch]$Remove)
    if (-not $Name) { throw "-GroupName 을 지정하세요." }
    $sku = Get-CopilotSku
    $safe = $Name.Replace("'", "''")
    $grp = Get-MgGroup -Filter "displayName eq '$safe'" -Property Id, DisplayName, SecurityEnabled
    if (-not $grp)            { throw "그룹을 찾을 수 없습니다: $Name" }
    if (@($grp).Count -gt 1)  { throw "동일 이름 그룹이 여러 개입니다: $Name" }
    if (-not $grp.SecurityEnabled) { throw "그룹 기반 라이선스는 보안 그룹만 가능합니다: $Name" }

    $op = if ($Remove) { 'RevokeGroup' } else { 'GrantGroup' }
    if ((Confirm-Change -Target $Name -Operation $op)) {
        try {
            if ($Remove) {
                Set-MgGroupLicense -GroupId $grp.Id -AddLicenses @() -RemoveLicenses @($sku.SkuId) | Out-Null
            } else {
                Set-MgGroupLicense -GroupId $grp.Id -AddLicenses @(@{ SkuId = $sku.SkuId }) -RemoveLicenses @() | Out-Null
            }
            Write-Log $Name $op 'OK' '구성원 반영까지 수 분 소요될 수 있음'
        } catch {
            Write-Log $Name $op 'FAIL' $_.Exception.Message
        }
    } else {
        Write-Log $Name $op 'WHATIF'
    }
}

function Sync-CopilotLicense {
    # CSV = 허용 목록(정답). 목록에 있으면 부여, 없으면(직접 할당분만) 회수
    $desired = Get-TargetUsers
    $current = Get-CopilotUsers
    $currentDirect = $current | Where-Object AssignedBy -eq 'Direct' |
                     ForEach-Object { $_.UserPrincipalName.ToLower() }
    $currentAll    = $current | ForEach-Object { $_.UserPrincipalName.ToLower() }

    $toGrant  = $desired       | Where-Object { $_ -notin $currentAll }
    $toRevoke = $currentDirect | Where-Object { $_ -notin $desired }

    Write-Host ("Sync 계획 → 부여 {0}명 / 회수 {1}명" -f @($toGrant).Count, @($toRevoke).Count) -ForegroundColor Magenta
    if ($toGrant)  { Grant-CopilotLicense  -Upns $toGrant }
    if ($toRevoke) { Revoke-CopilotLicense -Upns $toRevoke }
}
#endregion

#region ── 미사용자 조회 → 회수 여부 선택 (Inactive) ─────────────────
function Get-PropValue {
    # StrictMode 에서도 안전하게 속성 읽기 (없으면 $null)
    param($Obj, [string]$Name)
    $p = $Obj.PSObject.Properties[$Name]
    if ($p) { $p.Value } else { $null }
}

function Get-CopilotUsageReport {
    # Microsoft 365 Copilot 사용 현황 리포트 (사용자별 마지막 활동일)
    # ※ 현재 beta 엔드포인트. 리포트는 보통 2~3일 늦게 집계됨
    foreach ($period in 'D180', 'D90') {
        try {
            $uri  = "https://graph.microsoft.com/beta/reports/getMicrosoft365CopilotUsageUserDetail(period='$period')?`$format=application/json"
            $rows = [System.Collections.Generic.List[object]]::new()
            while ($uri) {
                $resp = Invoke-MgGraphRequest -Method GET -Uri $uri -OutputType PSObject
                foreach ($r in @(Get-PropValue $resp 'value')) { if ($r) { $rows.Add($r) } }
                $uri = Get-PropValue $resp '@odata.nextLink'
            }
            Write-Host "사용 리포트 수집: 기간 $period / $($rows.Count)행" -ForegroundColor DarkGray
            return [pscustomobject]@{ Period = $period; Rows = $rows }
        } catch {
            Write-Host "리포트($period) 조회 실패: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    throw "Copilot 사용 리포트를 가져오지 못했습니다. 'Reports Reader' 이상 역할과 Reports.Read.All 동의를 확인하세요."
}

function Find-InactiveCopilotUsers {
    $sku    = Get-CopilotSku
    $planIds = @($sku.ServicePlans.ServicePlanId)
    $report = Get-CopilotUsageReport
    $maxDays = if ($report.Period -eq 'D180') { 180 } else { 90 }
    if ($InactiveDays -gt $maxDays) {
        Write-Host "주의: 리포트는 최근 $maxDays일까지만 제공 → '$maxDays일 이상 미사용' 기준으로 판정합니다." -ForegroundColor Yellow
    }

    # 안전장치 1: 리포트 사용자명이 익명화(해시)되어 있으면 전원이 미사용으로 보이므로 중단
    $sample = @($report.Rows | Select-Object -First 20 | ForEach-Object { Get-PropValue $_ 'userPrincipalName' })
    if ($sample.Count -gt 0 -and -not ($sample | Where-Object { $_ -like '*@*' })) {
        throw ("리포트 사용자명이 익명화되어 있습니다. M365 관리 센터 > 설정 > 조직 설정 > 보고서 에서 " +
               "'보고서에 숨겨진 사용자, 그룹 및 사이트 이름 표시' 체크를 해제한 뒤 다시 실행하세요.")
    }

    # UPN → 마지막 활동일 / 리포트 기준일
    $lastByUpn = @{}
    $refDate   = (Get-Date).Date
    foreach ($r in $report.Rows) {
        $upn = Get-PropValue $r 'userPrincipalName'
        if (-not $upn) { continue }
        $lastByUpn[$upn.ToLower()] = Get-PropValue $r 'lastActivityDate'
        $rd = Get-PropValue $r 'reportRefreshDate'
        if ($rd) { $refDate = [datetime]$rd }   # 집계 지연만큼 억울한 판정을 막기 위해 리포트 기준일 사용
    }
    $cutoff  = $refDate.AddDays(-$InactiveDays)
    $exclude = @($Exclude | Where-Object { $_ } | ForEach-Object { $_.Trim().ToLower() })

    $users = Get-MgUser -All -ConsistencyLevel eventual -CountVariable cnt `
        -Filter "assignedLicenses/any(x:x/skuId eq $($sku.SkuId))" `
        -Property Id, DisplayName, UserPrincipalName, Department, AccountEnabled, AssignedPlans, LicenseAssignmentStates

    # 안전장치 2: 라이선스 보유자는 있는데 리포트가 비어 있으면 판정 불가
    if (@($users).Count -gt 0 -and $lastByUpn.Count -eq 0) {
        throw "라이선스 보유자는 $(@($users).Count)명인데 사용 리포트가 비어 있습니다. 집계 지연일 수 있으니 나중에 다시 실행하세요."
    }

    foreach ($u in $users) {
        $upn = $u.UserPrincipalName.ToLower()

        # Copilot 서비스 플랜이 처음 할당된 날짜 (신규 사용자 유예 판단용)
        $since = @($u.AssignedPlans | Where-Object { $_.ServicePlanId -in $planIds -and $_.AssignedDateTime } |
                   ForEach-Object { [datetime]$_.AssignedDateTime } | Sort-Object | Select-Object -First 1)
        $sinceDate = if ($since) { $since[0].Date } else { $null }

        $lastRaw  = if ($lastByUpn.ContainsKey($upn)) { $lastByUpn[$upn] } else { $null }
        $lastDate = if ($lastRaw) { ([datetime]$lastRaw).Date } else { $null }
        $daysIdle = if ($lastDate) { [int]($refDate - $lastDate).TotalDays } else { $null }

        $states  = @($u.LicenseAssignmentStates | Where-Object SkuId -eq $sku.SkuId)
        $byGroup = [bool]($states | Where-Object { $_.AssignedByGroup })

        $status = if ($upn -in $exclude)                         { '제외대상' }
                  elseif ($sinceDate -and $sinceDate -gt $cutoff) { '신규-유예' }
                  elseif (-not $lastDate)                         { '사용이력없음' }
                  elseif ($lastDate -lt $cutoff)                  { '미사용' }
                  else                                            { '사용중' }

        [pscustomobject]@{
            UserPrincipalName = $u.UserPrincipalName
            DisplayName       = $u.DisplayName
            Department        = $u.Department
            AccountEnabled    = $u.AccountEnabled
            AssignedBy        = if ($byGroup) { 'Group' } else { 'Direct' }
            LicenseSince      = if ($sinceDate) { $sinceDate.ToString('yyyy-MM-dd') } else { '' }
            LastActivity      = if ($lastDate)  { $lastDate.ToString('yyyy-MM-dd') }  else { '(없음)' }
            DaysInactive      = if ($null -ne $daysIdle) { $daysIdle } else { '' }
            Status            = $status
            Candidate         = $status -in '미사용', '사용이력없음'
        }
    }
}

function Select-ByNumber {
    # "1,3,5-8" 또는 "all" 형식 입력을 인덱스 목록으로 변환
    param([object[]]$Items)
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $it = $Items[$i]
        Write-Host ("{0,3}) {1,-40} {2,-12} 마지막활동 {3}  ({4})" -f ($i + 1), $it.UserPrincipalName,
                    $it.Department, $it.LastActivity, $it.AssignedBy)
    }
    $raw = Read-Host "회수할 번호 입력 (예: 1,3,5-8 / all / 엔터=취소)"
    if (-not $raw) { return @() }
    if ($raw.Trim() -eq 'all') { return $Items }
    $picked = foreach ($tok in $raw -split ',') {
        $t = $tok.Trim()
        if ($t -match '^(\d+)-(\d+)$') { [int]$Matches[1]..[int]$Matches[2] }
        elseif ($t -match '^\d+$')     { [int]$t }
        elseif ($t)                    { Write-Host "무시된 입력: $t" -ForegroundColor Yellow }
    }
    $picked | Where-Object { $_ -ge 1 -and $_ -le $Items.Count } | Sort-Object -Unique | ForEach-Object { $Items[$_ - 1] }
}

function Invoke-InactiveReview {
    $all = @(Find-InactiveCopilotUsers)

    # 1) 조회 결과 먼저 보여주기
    $all | Export-Csv -Path $ReportPath -NoTypeInformation -Encoding UTF8 -WhatIf:$false
    $summary = $all | Group-Object Status | Sort-Object Name | ForEach-Object { "$($_.Name) $($_.Count)명" }
    Write-Host ""
    Write-Host "=== Copilot 미사용 조회 결과 (기준: $InactiveDays일) ===" -ForegroundColor Magenta
    Write-Host "전체 $($all.Count)명 → $($summary -join ' / ')"
    Write-Host "전체 리포트 저장: $ReportPath" -ForegroundColor DarkGray

    $cands = @($all | Where-Object Candidate | Sort-Object { if ($_.DaysInactive -eq '') { 99999 } else { $_.DaysInactive } } -Descending)
    if (-not $cands) { Write-Host "회수 후보가 없습니다." -ForegroundColor Green; return }

    $cands | Format-Table UserPrincipalName, DisplayName, Department, LastActivity, DaysInactive, AssignedBy, Status -AutoSize

    $groupCnt = @($cands | Where-Object AssignedBy -eq 'Group').Count
    if ($groupCnt -and -not $RemoveFromGroup) {
        Write-Host "※ 그룹 상속 $groupCnt명은 -RemoveFromGroup 없이는 회수되지 않습니다." -ForegroundColor Yellow
    }

    # 2) 회수 여부 결정
    $targets = @()
    if ($AutoRevoke) {
        $targets = $cands
        Write-Host "AutoRevoke: 후보 $($cands.Count)명 전원 회수 진행" -ForegroundColor Yellow
    } elseif (-not [Environment]::UserInteractive) {
        Write-Host "비대화형 실행 → 조회만 수행했습니다. (회수하려면 -AutoRevoke)" -ForegroundColor Yellow
        return
    } else {
        Write-Host "권한을 회수할까요?  [A] 후보 전체 회수   [S] 골라서 회수   [N] 회수 안 함(조회만)" -ForegroundColor Cyan
        switch -Regex ((Read-Host "선택").Trim()) {
            '^[Aa]' { $targets = $cands }
            '^[Ss]' { $targets = @(Select-ByNumber -Items $cands) }
            default { Write-Host "회수하지 않고 종료합니다." -ForegroundColor Green; return }
        }
        if (-not $targets) { Write-Host "선택된 사용자가 없습니다." -ForegroundColor Green; return }

        if (@($targets | Where-Object AssignedBy -eq 'Group').Count -and -not $RemoveFromGroup) {
            if ((Read-Host "그룹 상속 사용자는 라이선스 그룹에서 제외해서 회수할까요? (Y/N)") -match '^[Yy]') {
                $script:RemoveFromGroup = [switch]$true
            }
        }
        $ok = Read-Host "$($targets.Count)명의 Copilot 권한을 회수합니다. 진행할까요? (Y/N)"
        if ($ok -notmatch '^[Yy]') { Write-Host "취소했습니다." -ForegroundColor Green; return }
    }

    # 3) 회수 실행 (-WhatIf 존중, 결과는 로그 CSV)
    Revoke-CopilotLicense -Upns @($targets.UserPrincipalName)
}
#endregion

#region ── Windows 로컬 Copilot 제어 ───────────────────────────────
function Get-WinPolicies {
    $Hive = if ($Scope -eq 'Machine') { 'HKLM:' } else { 'HKCU:' }
    @(
    # Windows Copilot (구형 사이드바) 끄기
    @{ Path = "$Hive\Software\Policies\Microsoft\Windows\WindowsCopilot"; Name = 'TurnOffWindowsCopilot'; Off = 1 }
    # Edge 사이드바(Copilot 버튼) 끄기 - Edge 정책은 HKLM/HKCU 모두 지원
    @{ Path = "$Hive\Software\Policies\Microsoft\Edge"; Name = 'HubsSidebarEnabled'; Off = 0 }
    )
}
$CopilotAppPattern = 'Microsoft.Copilot*'   # 신형 Copilot 앱 (M365 Copilot 앱 Microsoft.MicrosoftOfficeHub 는 제외)

function Disable-WindowsCopilot {
    Assert-Admin -Always
    foreach ($p in (Get-WinPolicies)) {
        if ((Confirm-Change -Target "$($p.Path)\$($p.Name)" -Operation "정책 설정 = $($p.Off)")) {
            try {
                if (-not (Test-Path $p.Path)) { New-Item -Path $p.Path -Force | Out-Null }
                New-ItemProperty -Path $p.Path -Name $p.Name -Value $p.Off -PropertyType DWord -Force | Out-Null
                Write-Log $p.Name 'WinDisable' 'OK' $p.Path
            } catch { Write-Log $p.Name 'WinDisable' 'FAIL' $_.Exception.Message }
        }
    }

    # 설치된 Copilot 앱 제거 (모든 사용자)
    $pkgs = Get-AppxPackage -AllUsers -Name $CopilotAppPattern -ErrorAction SilentlyContinue
    foreach ($pkg in $pkgs) {
        if ((Confirm-Change -Target $pkg.PackageFullName -Operation "앱 제거")) {
            try {
                Remove-AppxPackage -Package $pkg.PackageFullName -AllUsers
                Write-Log $pkg.Name 'WinDisable' 'OK' '앱 제거'
            } catch { Write-Log $pkg.Name 'WinDisable' 'FAIL' $_.Exception.Message }
        }
    }
    # 신규 사용자 프로필에 재설치되지 않도록 프로비저닝 패키지 제거
    $prov = Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like $CopilotAppPattern
    foreach ($pp in $prov) {
        if ((Confirm-Change -Target $pp.PackageName -Operation "프로비저닝 제거")) {
            try {
                Remove-AppxProvisionedPackage -Online -PackageName $pp.PackageName | Out-Null
                Write-Log $pp.DisplayName 'WinDisable' 'OK' '프로비저닝 제거'
            } catch { Write-Log $pp.DisplayName 'WinDisable' 'FAIL' $_.Exception.Message }
        }
    }
    Write-Host "적용 완료. 로그오프/재부팅 또는 'gpupdate /force' 후 반영됩니다." -ForegroundColor Green
}

function Enable-WindowsCopilot {
    Assert-Admin
    foreach ($p in (Get-WinPolicies)) {
        if ((Test-Path $p.Path) -and (Get-ItemProperty -Path $p.Path -Name $p.Name -ErrorAction SilentlyContinue)) {
            if ((Confirm-Change -Target "$($p.Path)\$($p.Name)" -Operation "정책 제거")) {
                Remove-ItemProperty -Path $p.Path -Name $p.Name -Force
                Write-Log $p.Name 'WinEnable' 'OK' $p.Path
            }
        } else {
            Write-Log $p.Name 'WinEnable' 'SKIP' '정책 없음'
        }
    }
    Write-Host "정책 해제 완료. Copilot 앱은 Microsoft Store 또는 'winget install 9NHT9RB2F4HD' 로 재설치하세요." -ForegroundColor Green
}

function Get-WindowsCopilotStatus {
    foreach ($h in 'HKLM:', 'HKCU:') {
        foreach ($p in (Get-WinPolicies)) {
            $path = $p.Path -replace '^HK(LM|CU):', $h
            $val  = (Get-ItemProperty -Path $path -Name $p.Name -ErrorAction SilentlyContinue).$($p.Name)
            [pscustomobject]@{ Hive = $h; Policy = $p.Name; Value = if ($null -eq $val) { '(미설정)' } else { $val } }
        }
    }
    $apps = Get-AppxPackage -Name $CopilotAppPattern -ErrorAction SilentlyContinue
    [pscustomobject]@{ Hive = 'App'; Policy = 'Copilot 앱(현재 사용자)'; Value = if ($apps) { $apps.Version -join ', ' } else { '미설치' } }
}

function Show-WindowsCopilotStatus { Get-WindowsCopilotStatus | Format-Table -AutoSize }
#endregion

#region ── 실행 ────────────────────────────────────────────────────
if (-not $Action) {
    # dot-source(. .\Manage-Copilot.ps1) 시에는 함수만 로드하고 종료
    if ($MyInvocation.InvocationName -ne '.') {
        Write-Host "사용법: .\Manage-Copilot.ps1 -Action <Status|List|Grant|Revoke|GrantGroup|RevokeGroup|Sync|Inactive|WinDisable|WinEnable|WinStatus>"
        Write-Host "화면(GUI): .\Copilot-Manager-GUI.ps1"
    }
    return
}
try {
    if ($Action -notlike 'Win*') { Connect-CopilotGraph }

    switch ($Action) {
        'Status'      { Show-Status }
        'List'        {
            $r = @(Get-CopilotUsers)
            $r | Sort-Object UserPrincipalName | Format-Table -AutoSize
            Write-Host "총 $($r.Count)명" -ForegroundColor DarkGray
        }
        'Grant'       { Grant-CopilotLicense  -Upns (Get-TargetUsers) }
        'Revoke'      { Revoke-CopilotLicense -Upns (Get-TargetUsers) }
        'GrantGroup'  { Set-CopilotGroupLicense -Name $GroupName }
        'RevokeGroup' { Set-CopilotGroupLicense -Name $GroupName -Remove }
        'Sync'        { Sync-CopilotLicense }
        'Inactive'    { Invoke-InactiveReview }
        'WinDisable'  { Disable-WindowsCopilot }
        'WinEnable'   { Enable-WindowsCopilot }
        'WinStatus'   { Show-WindowsCopilotStatus }
    }
    if ((Test-Path $LogPath) -and $Action -notin 'Status', 'List', 'WinStatus') {
        Write-Host "로그: $LogPath" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "오류: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
#endregion
