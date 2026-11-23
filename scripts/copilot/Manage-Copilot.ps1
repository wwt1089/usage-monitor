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

    [Windows 로컬 Copilot 제어 - 관리자 권한 필요]
      WinDisable   : Windows Copilot 정책 차단 + Copilot 앱 제거 + Edge 사이드바 차단
      WinEnable    : 위 정책 해제
      WinStatus    : 현재 로컬 정책/앱 상태 조회

    모든 변경 작업은 -WhatIf 로 사전 시뮬레이션 가능하며, 결과는 CSV 로그로 남습니다.

.EXAMPLE
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
    [Parameter(Mandatory)]
    [ValidateSet('Status', 'List', 'Grant', 'Revoke', 'GrantGroup', 'RevokeGroup', 'Sync',
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

    [string]$LogPath = (Join-Path $PSScriptRoot ("copilot-log-{0:yyyyMMdd}.csv" -f (Get-Date)))
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
    $color = switch ($Result) { 'OK' { 'Green' } 'SKIP' { 'Yellow' } 'WHATIF' { 'Cyan' } default { 'Red' } }
    Write-Host ("[{0}] {1,-12} {2,-40} {3}" -f $Result, $Operation, $Target, $Detail) -ForegroundColor $color
    if (-not $WhatIfPreference) {
        $row | Export-Csv -Path $LogPath -Append -NoTypeInformation -Encoding UTF8
    }
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
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin -and ($Scope -eq 'Machine' -or $Action -eq 'WinDisable')) {
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
    $scopes = 'User.ReadWrite.All', 'Group.ReadWrite.All', 'Organization.Read.All', 'Directory.Read.All'
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

function Show-Status {
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
    } | Format-List
}

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
            if ($PSCmdlet.ShouldProcess($upn, "Copilot 라이선스 부여")) {
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
                # 그룹 상속 라이선스는 사용자 단위로 회수 불가 → 그룹에서 제외해야 함
                $gNames = foreach ($g in $byGroup) { (Get-MgGroup -GroupId $g.AssignedByGroup).DisplayName }
                Write-Log $upn 'Revoke' 'SKIP' "그룹 상속 - 그룹에서 제외 필요: $($gNames -join ', ')"
                continue
            }
            if ($PSCmdlet.ShouldProcess($upn, "Copilot 라이선스 회수")) {
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
    if ($PSCmdlet.ShouldProcess($Name, $op)) {
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

#region ── Windows 로컬 Copilot 제어 ───────────────────────────────
$Hive = if ($Scope -eq 'Machine') { 'HKLM:' } else { 'HKCU:' }
$WinPolicies = @(
    # Windows Copilot (구형 사이드바) 끄기
    @{ Path = "$Hive\Software\Policies\Microsoft\Windows\WindowsCopilot"; Name = 'TurnOffWindowsCopilot'; Off = 1 }
    # Edge 사이드바(Copilot 버튼) 끄기 - Edge 정책은 HKLM/HKCU 모두 지원
    @{ Path = "$Hive\Software\Policies\Microsoft\Edge"; Name = 'HubsSidebarEnabled'; Off = 0 }
)
$CopilotAppPattern = 'Microsoft.Copilot*'   # 신형 Copilot 앱 (M365 Copilot 앱 Microsoft.MicrosoftOfficeHub 는 제외)

function Disable-WindowsCopilot {
    Assert-Admin
    foreach ($p in $WinPolicies) {
        if ($PSCmdlet.ShouldProcess("$($p.Path)\$($p.Name)", "정책 설정 = $($p.Off)")) {
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
        if ($PSCmdlet.ShouldProcess($pkg.PackageFullName, "앱 제거")) {
            try {
                Remove-AppxPackage -Package $pkg.PackageFullName -AllUsers
                Write-Log $pkg.Name 'WinDisable' 'OK' '앱 제거'
            } catch { Write-Log $pkg.Name 'WinDisable' 'FAIL' $_.Exception.Message }
        }
    }
    # 신규 사용자 프로필에 재설치되지 않도록 프로비저닝 패키지 제거
    $prov = Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like $CopilotAppPattern
    foreach ($pp in $prov) {
        if ($PSCmdlet.ShouldProcess($pp.PackageName, "프로비저닝 제거")) {
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
    foreach ($p in $WinPolicies) {
        if ((Test-Path $p.Path) -and (Get-ItemProperty -Path $p.Path -Name $p.Name -ErrorAction SilentlyContinue)) {
            if ($PSCmdlet.ShouldProcess("$($p.Path)\$($p.Name)", "정책 제거")) {
                Remove-ItemProperty -Path $p.Path -Name $p.Name -Force
                Write-Log $p.Name 'WinEnable' 'OK' $p.Path
            }
        } else {
            Write-Log $p.Name 'WinEnable' 'SKIP' '정책 없음'
        }
    }
    Write-Host "정책 해제 완료. Copilot 앱은 Microsoft Store 또는 'winget install 9NHT9RB2F4HD' 로 재설치하세요." -ForegroundColor Green
}

function Show-WindowsCopilotStatus {
    $rows = foreach ($h in 'HKLM:', 'HKCU:') {
        foreach ($p in $WinPolicies) {
            $path = $p.Path -replace '^HK(LM|CU):', $h
            $val  = (Get-ItemProperty -Path $path -Name $p.Name -ErrorAction SilentlyContinue).$($p.Name)
            [pscustomobject]@{ Hive = $h; Policy = $p.Name; Value = if ($null -eq $val) { '(미설정)' } else { $val } }
        }
    }
    $rows | Format-Table -AutoSize
    $apps = Get-AppxPackage -Name $CopilotAppPattern -ErrorAction SilentlyContinue
    Write-Host ("Copilot 앱 설치 여부(현재 사용자): {0}" -f $(if ($apps) { $apps.Version -join ', ' } else { '없음' }))
}
#endregion

#region ── 실행 ────────────────────────────────────────────────────
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
