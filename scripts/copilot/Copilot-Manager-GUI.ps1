<#
.SYNOPSIS
    Copilot 권한 관리 화면 (WPF GUI)

.DESCRIPTION
    Manage-Copilot.ps1 의 기능을 화면으로 제공합니다.
      [라이선스 현황]   구매/할당/잔여 수량, 보유자 목록
      [미사용자 정리]   N일 미사용자 '조회 우선' → 체크한 사용자만 회수
      [부여/회수]       UPN 입력 또는 CSV 로 부여/회수, 보안 그룹 라이선스
      [Windows Copilot] 로컬 PC 정책 상태/차단/해제

    하단 '시뮬레이션 모드'가 기본으로 켜져 있어 실제 변경 없이 결과만 로그로 확인합니다.
    실제 적용하려면 체크를 해제하세요.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Copilot-Manager-GUI.ps1

.NOTES
    Manage-Copilot.ps1 과 같은 폴더에 있어야 합니다. Windows 전용.
#>
[CmdletBinding()]
param()

#region ── 실행 환경 확인 ──────────────────────────────────────────
if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    Write-Error "이 화면은 Windows 에서만 실행됩니다."; return
}
# WPF 는 STA 스레드 필요 → 아니면 -STA 로 재실행
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    $exe = (Get-Process -Id $PID).Path
    Start-Process -FilePath $exe -ArgumentList @('-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    return
}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$corePath = Join-Path $PSScriptRoot 'Manage-Copilot.ps1'
if (-not (Test-Path $corePath)) {
    [System.Windows.MessageBox]::Show("Manage-Copilot.ps1 을 찾을 수 없습니다.`n$corePath", '오류') | Out-Null
    return
}
. $corePath                       # 함수/기본 설정만 로드 (-Action 없음)
$script:GuiMode      = $true
$script:GuiWhatIf    = $true
$script:InactiveRows = @()
$script:InactiveContext = $null
$script:UiResults = $null
#endregion

#region ── 화면 정의 (XAML) ─────────────────────────────────────────
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Copilot 권한 관리" Width="1100" Height="760" MinWidth="900" MinHeight="600"
        WindowStartupLocation="CenterScreen" FontFamily="Malgun Gothic" FontSize="13" Background="#F5F6F8">
  <Window.Resources>
    <Style TargetType="Button">
      <Setter Property="Padding" Value="12,5"/>
      <Setter Property="Margin" Value="0,0,8,0"/>
      <Setter Property="MinWidth" Value="90"/>
    </Style>
    <Style TargetType="TextBox">
      <Setter Property="Padding" Value="4,3"/>
      <Setter Property="VerticalContentAlignment" Value="Center"/>
    </Style>
    <Style x:Key="Danger" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}">
      <Setter Property="Background" Value="#C62828"/>
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
    </Style>
    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background" Value="White"/>
      <Setter Property="BorderBrush" Value="#DDE1E6"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius" Value="6"/>
      <Setter Property="Padding" Value="16,10"/>
      <Setter Property="Margin" Value="0,0,12,0"/>
      <Setter Property="MinWidth" Value="150"/>
    </Style>
    <Style TargetType="DataGrid">
      <Setter Property="AutoGenerateColumns" Value="False"/>
      <Setter Property="CanUserAddRows" Value="False"/>
      <Setter Property="CanUserDeleteRows" Value="False"/>
      <Setter Property="HeadersVisibility" Value="Column"/>
      <Setter Property="GridLinesVisibility" Value="Horizontal"/>
      <Setter Property="HorizontalGridLinesBrush" Value="#EEEEEE"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="RowHeight" Value="26"/>
    </Style>
  </Window.Resources>

  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="150"/>
    </Grid.RowDefinitions>

    <!-- 상단: 연결 -->
    <DockPanel Grid.Row="0" Margin="0,0,0,10">
      <TextBlock Text="Copilot 권한 관리" FontSize="20" FontWeight="Bold" VerticalAlignment="Center" Margin="0,0,24,0"/>
      <TextBlock Text="SKU" VerticalAlignment="Center" Margin="0,0,6,0"/>
      <TextBox x:Name="txtSku" Width="200" Margin="0,0,8,0"/>
      <Button x:Name="btnConnect" Content="Graph 연결"/>
      <TextBlock x:Name="txtAccount" Text="미연결" VerticalAlignment="Center" Foreground="#666"/>
    </DockPanel>

    <TabControl x:Name="tabs" Grid.Row="1">

      <!-- 탭1: 라이선스 현황 -->
      <TabItem Header="  라이선스 현황  ">
        <Grid Margin="10">
          <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
          <StackPanel Orientation="Horizontal" Margin="0,0,0,10">
            <Button x:Name="btnStatus" Content="현황 새로고침"/>
            <Button x:Name="btnList" Content="보유자 목록 조회"/>
          </StackPanel>
          <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,0,0,10">
            <Border Style="{StaticResource Card}"><StackPanel>
              <TextBlock Text="구매" Foreground="#666"/><TextBlock x:Name="txtPurchased" Text="-" FontSize="24" FontWeight="Bold"/>
            </StackPanel></Border>
            <Border Style="{StaticResource Card}"><StackPanel>
              <TextBlock Text="할당" Foreground="#666"/><TextBlock x:Name="txtAssigned" Text="-" FontSize="24" FontWeight="Bold"/>
            </StackPanel></Border>
            <Border Style="{StaticResource Card}"><StackPanel>
              <TextBlock Text="잔여" Foreground="#666"/><TextBlock x:Name="txtAvailable" Text="-" FontSize="24" FontWeight="Bold" Foreground="#2E7D32"/>
            </StackPanel></Border>
            <Border Style="{StaticResource Card}"><StackPanel>
              <TextBlock Text="보유자 목록" Foreground="#666"/><TextBlock x:Name="txtUserCount" Text="-" FontSize="24" FontWeight="Bold"/>
            </StackPanel></Border>
          </StackPanel>
          <DataGrid x:Name="gridUsers" Grid.Row="2" IsReadOnly="True">
            <DataGrid.Columns>
              <DataGridTextColumn Header="UPN" Binding="{Binding UserPrincipalName}" Width="2*"/>
              <DataGridTextColumn Header="이름" Binding="{Binding DisplayName}" Width="*"/>
              <DataGridTextColumn Header="부서" Binding="{Binding Department}" Width="*"/>
              <DataGridTextColumn Header="계정활성" Binding="{Binding Enabled}" Width="80"/>
              <DataGridTextColumn Header="할당방식" Binding="{Binding AssignedBy}" Width="80"/>
              <DataGridTextColumn Header="상태" Binding="{Binding State}" Width="90"/>
            </DataGrid.Columns>
          </DataGrid>
        </Grid>
      </TabItem>

      <!-- 탭2: 미사용자 정리 -->
      <TabItem Header="  미사용자 정리  ">
        <Grid Margin="10">
          <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
          <StackPanel Orientation="Horizontal" Margin="0,0,0,8">
            <TextBlock Text="미사용 기준" VerticalAlignment="Center" Margin="0,0,6,0"/>
            <TextBox x:Name="txtDays" Text="30" Width="50" HorizontalContentAlignment="Center"/>
            <TextBlock Text="일 이상" VerticalAlignment="Center" Margin="6,0,20,0"/>
            <TextBlock Text="제외 사용자(쉼표 구분)" VerticalAlignment="Center" Margin="0,0,6,0"/>
            <TextBox x:Name="txtExclude" Width="300" Margin="0,0,12,0"/>
            <Button x:Name="btnFind" Content="① 조회" FontWeight="SemiBold"/>
          </StackPanel>
          <DockPanel Grid.Row="1" Margin="0,0,0,8">
            <CheckBox x:Name="chkOnlyCand" Content="회수 후보만 보기" IsChecked="True" VerticalAlignment="Center" Margin="0,0,16,0"/>
            <TextBlock x:Name="txtSummary" Text="조회 전입니다. 기준 일수를 정하고 ① 조회를 누르세요." VerticalAlignment="Center" Foreground="#444"/>
          </DockPanel>
          <DataGrid x:Name="gridInactive" Grid.Row="2">
            <DataGrid.RowStyle>
              <Style TargetType="DataGridRow">
                <Style.Triggers>
                  <DataTrigger Binding="{Binding Status}" Value="미사용"><Setter Property="Background" Value="#FFEBEE"/></DataTrigger>
                  <DataTrigger Binding="{Binding Status}" Value="사용이력없음"><Setter Property="Background" Value="#FFF3E0"/></DataTrigger>
                  <DataTrigger Binding="{Binding Status}" Value="신규-유예"><Setter Property="Background" Value="#E3F2FD"/></DataTrigger>
                  <DataTrigger Binding="{Binding Status}" Value="제외대상"><Setter Property="Foreground" Value="#999"/></DataTrigger>
                <DataTrigger Binding="{Binding Status}" Value="판정 불가"><Setter Property="Foreground" Value="#795548"/></DataTrigger>
                </Style.Triggers>
              </Style>
            </DataGrid.RowStyle>
            <DataGrid.Columns>
              <DataGridTemplateColumn Header="회수" Width="50">
                <DataGridTemplateColumn.CellTemplate>
                  <DataTemplate>
                    <CheckBox IsChecked="{Binding Selected, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}" IsEnabled="{Binding Candidate}" HorizontalAlignment="Center" VerticalAlignment="Center"/>
                  </DataTemplate>
                </DataGridTemplateColumn.CellTemplate>
              </DataGridTemplateColumn>
              <DataGridTextColumn Header="UPN" Binding="{Binding UserPrincipalName}" Width="2*" IsReadOnly="True"/>
              <DataGridTextColumn Header="이름" Binding="{Binding DisplayName}" Width="*" IsReadOnly="True"/>
              <DataGridTextColumn Header="부서" Binding="{Binding Department}" Width="*" IsReadOnly="True"/>
              <DataGridTextColumn Header="라이선스 시작" Binding="{Binding LicenseSince}" Width="100" IsReadOnly="True"/>
              <DataGridTextColumn Header="마지막 사용" Binding="{Binding LastActivity}" Width="100" IsReadOnly="True"/>
              <DataGridTextColumn Header="미사용 일수" Binding="{Binding DaysInactive}" Width="85" IsReadOnly="True"/>
              <DataGridTextColumn Header="할당방식" Binding="{Binding AssignedBy}" Width="75" IsReadOnly="True"/>
              <DataGridTextColumn Header="판정" Binding="{Binding Status}" Width="95" IsReadOnly="True"/>
            </DataGrid.Columns>
          </DataGrid>
          <DockPanel Grid.Row="3" Margin="0,10,0,0" LastChildFill="False">
            <Button x:Name="btnSelCand" Content="후보 전체 체크"/>
            <Button x:Name="btnSelNone" Content="체크 해제"/>
            <Button x:Name="btnExport" Content="CSV 저장"/>
            <Button x:Name="btnRemoveGroup" Content="선택 사용자 그룹에서 제외" Style="{StaticResource Danger}"/>
            <Button x:Name="btnRevokeSel" DockPanel.Dock="Right" Content="② 직접 할당 회수" Style="{StaticResource Danger}" Margin="0"/>
          </DockPanel>
        </Grid>
      </TabItem>

      <!-- 탭3: 부여/회수 -->
      <TabItem Header="  부여 / 회수  ">
        <Grid Margin="10">
          <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="20"/><ColumnDefinition Width="360"/></Grid.ColumnDefinitions>
          <DockPanel>
            <TextBlock DockPanel.Dock="Top" Text="대상 사용자 UPN (줄바꿈/쉼표 구분)" Margin="0,0,0,6" FontWeight="SemiBold"/>
            <StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal" Margin="0,10,0,0">
              <Button x:Name="btnLoadCsv" Content="CSV 불러오기"/>
              <TextBlock Text="사용 위치" VerticalAlignment="Center" Margin="8,0,6,0"/>
              <TextBox x:Name="txtUsageLoc" Text="KR" Width="40" Margin="0,0,16,0" HorizontalContentAlignment="Center"/>
              <Button x:Name="btnGrant" Content="라이선스 부여" FontWeight="SemiBold"/>
              <Button x:Name="btnRevoke" Content="라이선스 회수" Style="{StaticResource Danger}"/>
            </StackPanel>
            <TextBox x:Name="txtUpns" AcceptsReturn="True" TextWrapping="NoWrap" VerticalScrollBarVisibility="Auto"
                     VerticalContentAlignment="Top" FontFamily="Consolas"/>
          </DockPanel>
          <GroupBox Grid.Column="2" Header="보안 그룹 라이선스 (권장 운영 방식)" Padding="10" VerticalAlignment="Top">
            <StackPanel>
              <TextBlock Text="그룹 이름" Margin="0,0,0,4"/>
              <TextBox x:Name="txtGroup" Margin="0,0,0,10"/>
              <StackPanel Orientation="Horizontal">
                <Button x:Name="btnGrantGroup" Content="그룹에 부여"/>
                <Button x:Name="btnRevokeGroup" Content="그룹에서 회수" Style="{StaticResource Danger}"/>
              </StackPanel>
              <TextBlock TextWrapping="Wrap" Foreground="#666" Margin="0,10,0,0"
                         Text="그룹에 라이선스를 걸어두면 이후엔 그룹 구성원 추가/제외만으로 권한이 관리됩니다. 구성원 반영까지 수 분 걸릴 수 있습니다."/>
            </StackPanel>
          </GroupBox>
        </Grid>
      </TabItem>

      <!-- 탭4: Windows Copilot -->
      <TabItem Header="  Windows Copilot (이 PC)  ">
        <Grid Margin="10">
          <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
          <StackPanel Orientation="Horizontal" Margin="0,0,0,8">
            <TextBlock Text="적용 범위" VerticalAlignment="Center" Margin="0,0,8,0"/>
            <RadioButton x:Name="rbMachine" Content="이 PC 전체 (HKLM)" IsChecked="True" VerticalAlignment="Center" Margin="0,0,12,0"/>
            <RadioButton x:Name="rbUser" Content="현재 사용자 (HKCU)" VerticalAlignment="Center" Margin="0,0,20,0"/>
            <Button x:Name="btnWinStatus" Content="상태 조회"/>
            <Button x:Name="btnWinDisable" Content="Copilot 차단" Style="{StaticResource Danger}"/>
            <Button x:Name="btnWinEnable" Content="차단 해제"/>
          </StackPanel>
          <TextBlock x:Name="txtAdmin" Grid.Row="1" Margin="0,0,0,8" TextWrapping="Wrap"/>
          <DataGrid x:Name="gridWin" Grid.Row="2" IsReadOnly="True">
            <DataGrid.Columns>
              <DataGridTextColumn Header="위치" Binding="{Binding Hive}" Width="80"/>
              <DataGridTextColumn Header="정책 / 항목" Binding="{Binding Policy}" Width="*"/>
              <DataGridTextColumn Header="값" Binding="{Binding Value}" Width="*"/>
            </DataGrid.Columns>
          </DataGrid>
        </Grid>
      </TabItem>
    </TabControl>

    <!-- 하단: 모드/상태 -->
    <DockPanel Grid.Row="2" Margin="0,10,0,6">
      <CheckBox x:Name="chkWhatIf" IsChecked="True" VerticalAlignment="Center" Margin="0,0,16,0">
        <TextBlock Text="시뮬레이션 모드 (실제 변경 안 함)" FontWeight="SemiBold" Foreground="#1565C0"/>
      </CheckBox>
      <Button x:Name="btnOpenFolder" DockPanel.Dock="Right" Content="로그 폴더 열기" Margin="0"/>
      <TextBlock x:Name="txtStatus" Text="준비" VerticalAlignment="Center" Foreground="#555"/>
    </DockPanel>
    <TextBox x:Name="txtLog" Grid.Row="3" IsReadOnly="True" FontFamily="Consolas" FontSize="12"
             VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto" TextWrapping="NoWrap"
             VerticalContentAlignment="Top" Background="#1E1E1E" Foreground="#DDDDDD"/>
  </Grid>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Load([System.Xml.XmlNodeReader]::new($xaml))
$ui  = @{}
$xaml.SelectNodes('//*[@*[local-name()="Name"]]') | ForEach-Object {
    $n = $_.GetAttribute('Name', 'http://schemas.microsoft.com/winfx/2006/xaml')
    if ($n) { $ui[$n] = $win.FindName($n) }
}
#endregion

#region ── 화면 공통 함수 ───────────────────────────────────────────
function Write-UiLog {
    param([string]$Text)
    $ui.txtLog.AppendText(("{0:HH:mm:ss}  {1}`r`n" -f (Get-Date), $Text))
    $ui.txtLog.ScrollToEnd()
}

# 핵심 스크립트의 Write-Host / Write-Log 출력을 화면 로그로 돌림
function Write-Host {
    param([Parameter(Position = 0, ValueFromRemainingArguments = $true)]$Object,
          $ForegroundColor, $BackgroundColor, [switch]$NoNewline)
    Write-UiLog (@($Object) -join ' ')
}
$script:LogSink = {
    param($line, $result)
    Write-UiLog $line
    if ($null -ne $script:UiResults -and $script:UiResults.ContainsKey($result)) {
        $script:UiResults[$result]++
    }
}

function Show-Msg {
    param([string]$Text, [string]$Title = '안내', [string]$Icon = 'Information', [string]$Buttons = 'OK')
    [System.Windows.MessageBox]::Show($win, $Text, $Title,
        [System.Windows.MessageBoxButton]$Buttons, [System.Windows.MessageBoxImage]$Icon)
}

function Confirm-Ui {
    param([string]$Text)
    $mode = if ($ui.chkWhatIf.IsChecked) { "`n`n[시뮬레이션 모드] 실제 변경되지 않습니다." }
            else { "`n`n[실제 적용] 즉시 반영됩니다." }
    (Show-Msg -Text ($Text + $mode) -Title '확인' -Icon 'Warning' -Buttons 'YesNo') -eq 'Yes'
}

# 화면 입력값 → 핵심 스크립트 설정 변수로 반영
function Sync-Settings {
    $script:SkuPartNumber = $ui.txtSku.Text.Trim()
    $script:GuiWhatIf     = [bool]$ui.chkWhatIf.IsChecked
    $d = 0
    if (-not [int]::TryParse($ui.txtDays.Text.Trim(), [ref]$d) -or $d -lt 1 -or $d -gt 365) {
        throw "미사용 기준 일수는 1~365 사이 숫자로 입력하세요."
    }
    $script:InactiveDays    = $d
    $script:Exclude         = @($ui.txtExclude.Text -split '[,;\s]+' | Where-Object { $_ })
    $script:Scope           = if ($ui.rbMachine.IsChecked) { 'Machine' } else { 'User' }
    $loc = $ui.txtUsageLoc.Text.Trim().ToUpper()
    if ($loc -notmatch '^[A-Z]{2}$') { throw "사용 위치는 2자리 국가 코드로 입력하세요. (예: KR)" }
    $script:UsageLocation = $loc
}

function Update-Account {
    $ctx = Get-MgContext -ErrorAction SilentlyContinue
    $ui.txtAccount.Text = if ($ctx) { "연결됨: $($ctx.Account) ($($ctx.TenantId))" } else { '미연결' }
}

# 작업 실행 래퍼: 대기 커서, 입력 잠금, 오류 메시지 처리
function Invoke-Ui {
    param([string]$Title, [scriptblock]$Work, [string]$GraphOperation, [switch]$SummarizeChanges)
    $win.Cursor = [System.Windows.Input.Cursors]::Wait
    $ui.tabs.IsEnabled = $false
    $ui.btnConnect.IsEnabled = $false
    $ui.txtSku.IsEnabled = $false
    $ui.chkWhatIf.IsEnabled = $false
    if ($SummarizeChanges) { $script:UiResults = @{ OK = 0; FAIL = 0; SKIP = 0; WHATIF = 0 } }
    $ui.txtStatus.Text = "$Title 진행 중..."
    # 상태 문구가 먼저 그려지도록 화면 갱신
    $win.Dispatcher.Invoke([action] {}, [System.Windows.Threading.DispatcherPriority]::Background)
    try {
        Sync-Settings
        if ($GraphOperation) { Connect-CopilotGraph -Operation $GraphOperation; Update-Account }
        & $Work
        if ($SummarizeChanges) {
            $result = $script:UiResults
            $ui.txtStatus.Text = "$Title · 성공 $($result.OK) / 실패 $($result.FAIL) / 건너뜀 $($result.SKIP) / 시뮬레이션 $($result.WHATIF)"
            Write-UiLog $ui.txtStatus.Text
        } else {
            $ui.txtStatus.Text = "$Title 완료 ($(Get-Date -Format 'HH:mm:ss'))"
        }
    } catch {
        Write-UiLog "오류: $($_.Exception.Message)"
        $ui.txtStatus.Text = "$Title 실패"
        Show-Msg -Text $_.Exception.Message -Title "$Title 실패" -Icon 'Error' | Out-Null
    } finally {
        $script:UiResults = $null
        $ui.tabs.IsEnabled = $true
        $ui.btnConnect.IsEnabled = $true
        $ui.txtSku.IsEnabled = $true
        $ui.chkWhatIf.IsEnabled = $true
        $win.Cursor = $null
    }
}

function Get-UpnsFromText {
    @($ui.txtUpns.Text -split '[\r\n,;\s]+' | Where-Object { $_ -like '*@*' } |
      ForEach-Object { $_.Trim().ToLower() } | Sort-Object -Unique)
}

function Update-InactiveGrid {
    $rows = if ($ui.chkOnlyCand.IsChecked) { $script:InactiveRows | Where-Object Candidate } else { $script:InactiveRows }
    $ui.gridInactive.ItemsSource = @($rows | Where-Object { $null -ne $_ })
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
#endregion

#region ── 이벤트: 상단/공통 ───────────────────────────────────────
$ui.txtSku.Text = $SkuPartNumber

$ui.btnConnect.Add_Click({
    Invoke-Ui 'Graph 연결' -GraphOperation Status {}
})

$ui.chkWhatIf.Add_Click({
    if (-not $ui.chkWhatIf.IsChecked) {
        $ok = Show-Msg -Text "시뮬레이션 모드를 끄면 이후 작업이 실제로 적용됩니다. 계속할까요?" -Title '실제 적용 모드' -Icon 'Warning' -Buttons 'YesNo'
        if ($ok -ne 'Yes') { $ui.chkWhatIf.IsChecked = $true; return }
        Write-UiLog '실제 적용 모드로 전환했습니다.'
    } else {
        Write-UiLog '시뮬레이션 모드로 전환했습니다.'
    }
})

$ui.btnOpenFolder.Add_Click({ Start-Process explorer.exe $PSScriptRoot })
#endregion

#region ── 이벤트: 라이선스 현황 ───────────────────────────────────
$ui.btnStatus.Add_Click({
    Invoke-Ui '현황 조회' -GraphOperation Status {
        $st = Get-CopilotStatus
        $ui.txtPurchased.Text = $st.Purchased
        $ui.txtAssigned.Text  = $st.Assigned
        $ui.txtAvailable.Text = $st.Available
        Write-UiLog ("{0}: 구매 {1} / 할당 {2} / 잔여 {3}" -f $st.SkuPartNumber, $st.Purchased, $st.Assigned, $st.Available)
    }
})

$ui.btnList.Add_Click({
    Invoke-Ui '보유자 조회' -GraphOperation List {
        $rows = @(Get-CopilotUsers | Sort-Object UserPrincipalName)
        $ui.gridUsers.ItemsSource = $rows
        $ui.txtUserCount.Text = $rows.Count
        Write-UiLog "Copilot 보유자 $($rows.Count)명"
    }
})
#endregion

#region ── 이벤트: 미사용자 정리 ───────────────────────────────────
$ui.btnFind.Add_Click({
    $script:InactiveRows = @()
    $script:InactiveContext = $null
    Update-InactiveGrid
    $ui.txtSummary.Text = '조회 결과가 없습니다.'
    Invoke-Ui '미사용자 조회' -GraphOperation Inactive {
        # 조회 우선: 기본은 아무도 체크하지 않음 → 사람이 확인 후 체크
        $script:InactiveRows = @(Find-InactiveCopilotUsers | ForEach-Object {
            $_ | Add-Member -NotePropertyName Selected -NotePropertyValue $false -PassThru
        })
        $ctx = Get-MgContext
        $script:InactiveContext = @{
            TenantId = $ctx.TenantId
            Account = $ctx.Account
            Sku = $SkuPartNumber
        }
        Update-InactiveGrid
        $cand = @($script:InactiveRows | Where-Object Candidate).Count
        $sum  = ($script:InactiveRows | Group-Object Status | Sort-Object Name |
                 ForEach-Object { "$($_.Name) $($_.Count)" }) -join ' · '
        $ui.txtSummary.Text = "전체 $($script:InactiveRows.Count)명 → $sum   |   회수 후보 ${cand}명 (체크 후 ② 실행)"
        Write-UiLog "미사용자 조회 완료: 기준 ${InactiveDays}일, 후보 ${cand}명"
    }
})

$ui.chkOnlyCand.Add_Click({ Update-InactiveGrid })

$ui.btnSelCand.Add_Click({
    foreach ($r in $script:InactiveRows) { if ($r.Candidate) { $r.Selected = $true } }
    $ui.gridInactive.Items.Refresh()
})

$ui.btnSelNone.Add_Click({
    foreach ($r in $script:InactiveRows) { $r.Selected = $false }
    $ui.gridInactive.Items.Refresh()
})

$ui.btnExport.Add_Click({
    if (-not $script:InactiveRows.Count) { Show-Msg '먼저 조회하세요.' | Out-Null; return }
    $dlg = New-Object Microsoft.Win32.SaveFileDialog
    $dlg.Filter   = 'CSV 파일 (*.csv)|*.csv'
    $dlg.FileName = "copilot-inactive-{0:yyyyMMdd}.csv" -f (Get-Date)
    if ($dlg.ShowDialog($win)) {
        try {
            $script:InactiveRows | Select-Object * -ExcludeProperty Selected |
                Export-Csv -Path $dlg.FileName -NoTypeInformation -Encoding UTF8 -WhatIf:$false
            Write-UiLog "CSV 저장: $($dlg.FileName)"
        } catch { Show-Msg -Text $_.Exception.Message -Title '저장 실패' -Icon 'Error' | Out-Null }
    }
})

function Get-CurrentInactiveTargets {
    param([object[]]$Selected)

    $ctx = Get-MgContext
    if (-not $script:InactiveContext -or $script:InactiveContext.Sku -ne $SkuPartNumber -or
        $script:InactiveContext.TenantId -ne $ctx.TenantId -or $script:InactiveContext.Account -ne $ctx.Account) {
        throw 'SKU 또는 Graph 연결이 변경됐습니다. 미사용자를 다시 조회하세요.'
    }
    $freshCandidates = @(Find-InactiveCopilotUsers | Where-Object Candidate |
                        Select-Object -ExpandProperty UserPrincipalName)
    foreach ($row in $Selected) {
        if ($row.UserPrincipalName -in $freshCandidates) {
            $row.UserPrincipalName
        } else {
            Write-Log $row.UserPrincipalName 'InactiveReview' 'SKIP' '최신 조회 결과에서 회수 후보가 아님'
        }
    }
}

function Confirm-GroupRemoval {
    param([object[]]$Plan)

    $dialog = New-Object System.Windows.Window
    $dialog.Title = '그룹 멤버십 제거 확인'
    $dialog.Owner = $win
    $dialog.Width = 760
    $dialog.Height = 420
    $dialog.WindowStartupLocation = 'CenterOwner'

    $panel = New-Object System.Windows.Controls.DockPanel
    $panel.Margin = '12'
    $dialog.Content = $panel
    $summary = New-Object System.Windows.Controls.TextBlock
    $summary.Text = "$($Plan.Count)건의 그룹 멤버십을 제거합니다. 그룹의 다른 라이선스나 접근 권한도 영향을 받을 수 있습니다."
    $summary.TextWrapping = 'Wrap'
    [System.Windows.Controls.DockPanel]::SetDock($summary, 'Top')
    [void]$panel.Children.Add($summary)

    $button = New-Object System.Windows.Controls.Button
    $button.Content = if ($script:GuiWhatIf) { '시뮬레이션' } else { '그룹에서 제외' }
    $button.Margin = '0,8,0,0'
    $button.Add_Click({ $dialog.DialogResult = $true }.GetNewClosure())
    [System.Windows.Controls.DockPanel]::SetDock($button, 'Bottom')
    [void]$panel.Children.Add($button)

    $list = New-Object System.Windows.Controls.ListBox
    $list.ItemsSource = @($Plan | ForEach-Object { "$($_.Upn) ← $($_.GroupName) [$($_.GroupId)]" })
    [void]$panel.Children.Add($list)
    return ($dialog.ShowDialog() -eq $true)
}

$ui.btnRevokeSel.Add_Click({
    $sel = @($script:InactiveRows | Where-Object { $_.Selected -and $_.Candidate })
    if (-not $sel.Count) { Show-Msg '회수할 후보를 체크하세요.' | Out-Null; return }

    $preview = ($sel | Select-Object -First 10 | ForEach-Object { "   - $($_.UserPrincipalName)" }) -join "`n"
    if ($sel.Count -gt 10) { $preview += "`n   ... 외 $($sel.Count - 10)명" }
    if (-not (Confirm-Ui ("$($sel.Count)명의 Copilot 직접 할당분을 회수합니다.`n`n" + $preview))) { return }
    Invoke-Ui '직접 할당 회수' -GraphOperation Inactive -SummarizeChanges {
        $targets = @(Get-CurrentInactiveTargets -Selected $sel)
        if ($targets.Count) {
            Connect-CopilotGraph -Operation Revoke
            Update-Account
            Revoke-CopilotLicense -Upns $targets
        }
    }
})

$ui.btnRemoveGroup.Add_Click({
    $sel = @($script:InactiveRows | Where-Object { $_.Selected -and $_.Candidate -and $_.AssignedBy -in 'Group', 'Mixed' })
    if (-not $sel.Count) { Show-Msg '그룹에서 제외할 회수 후보를 체크하세요.' | Out-Null; return }
    Invoke-Ui '그룹 멤버십 제거' -GraphOperation Inactive -SummarizeChanges {
        $targets = @(Get-CurrentInactiveTargets -Selected $sel)
        if (-not $targets.Count) { return }
        Connect-CopilotGraph -Operation GroupRemovalPreview
        $plan = @(Get-CopilotGroupRemovalPlan -Upns $targets)
        if (-not $plan.Count) { Show-Msg '제외할 수 있는 그룹을 찾지 못했습니다.' | Out-Null; return }
        if (-not (Confirm-GroupRemoval -Plan $plan)) { return }
        Connect-CopilotGraph -Operation GroupRemoval
        Update-Account
        Remove-CopilotGroupMembers -Plan $plan
    }
})
#endregion

#region ── 이벤트: 부여/회수 ──────────────────────────────────────
$ui.btnLoadCsv.Add_Click({
    $dlg = New-Object Microsoft.Win32.OpenFileDialog
    $dlg.Filter = 'CSV 파일 (*.csv)|*.csv'
    if ($dlg.ShowDialog($win)) {
        try {
            $csv = @(Import-Csv $dlg.FileName)
            if (-not ($csv | Get-Member -Name UserPrincipalName)) { throw "CSV 에 'UserPrincipalName' 컬럼이 필요합니다." }
            $ui.txtUpns.Text = (@(Get-UpnsFromText) + @($csv.UserPrincipalName) | Where-Object { $_ } |
                                Sort-Object -Unique) -join "`r`n"
            Write-UiLog "CSV 불러옴: $($csv.Count)행"
        } catch { Show-Msg -Text $_.Exception.Message -Title 'CSV 오류' -Icon 'Error' | Out-Null }
    }
})

$ui.btnGrant.Add_Click({
    $upns = Get-UpnsFromText
    if (-not $upns.Count) { Show-Msg '대상 UPN 을 입력하세요.' | Out-Null; return }
    if (-not (Confirm-Ui "$($upns.Count)명에게 Copilot 라이선스를 부여합니다.")) { return }
    Invoke-Ui '라이선스 부여' -GraphOperation Grant -SummarizeChanges { Grant-CopilotLicense -Upns $upns }
})

$ui.btnRevoke.Add_Click({
    $upns = Get-UpnsFromText
    if (-not $upns.Count) { Show-Msg '대상 UPN 을 입력하세요.' | Out-Null; return }
    if (-not (Confirm-Ui "$($upns.Count)명의 Copilot 라이선스를 회수합니다.")) { return }
    Invoke-Ui '라이선스 회수' -GraphOperation Revoke -SummarizeChanges { Revoke-CopilotLicense -Upns $upns }
})

$ui.btnGrantGroup.Add_Click({
    $g = $ui.txtGroup.Text.Trim()
    if (-not $g) { Show-Msg '그룹 이름을 입력하세요.' | Out-Null; return }
    if (-not (Confirm-Ui "그룹 '$g' 에 Copilot 라이선스를 부여합니다.")) { return }
    Invoke-Ui '그룹 부여' -GraphOperation GrantGroup -SummarizeChanges { Set-CopilotGroupLicense -Name $g }
})

$ui.btnRevokeGroup.Add_Click({
    $g = $ui.txtGroup.Text.Trim()
    if (-not $g) { Show-Msg '그룹 이름을 입력하세요.' | Out-Null; return }
    if (-not (Confirm-Ui "그룹 '$g' 의 Copilot 라이선스를 회수합니다.`n구성원이 이 그룹에서 상속받은 Copilot 라이선스가 제거됩니다.")) { return }
    Invoke-Ui '그룹 회수' -GraphOperation RevokeGroup -SummarizeChanges { Set-CopilotGroupLicense -Name $g -Remove }
})
#endregion

#region ── 이벤트: Windows Copilot ────────────────────────────────
$isAdmin = Test-IsAdmin
$ui.txtAdmin.Text = if ($isAdmin) { '관리자 권한으로 실행 중입니다.' }
                    else { '⚠ 관리자 권한이 아닙니다. PC 전체 정책과 앱을 변경하려면 관리자 권한으로 실행하세요.' }
$ui.txtAdmin.Foreground = if ($isAdmin) { 'Green' } else { 'DarkOrange' }

$ui.btnWinStatus.Add_Click({
    Invoke-Ui 'Windows 상태 조회' { $ui.gridWin.ItemsSource = @(Get-WindowsCopilotStatus) }
})

$ui.btnWinDisable.Add_Click({
    $scopeLabel = if ($ui.rbMachine.IsChecked) { '이 PC의 모든 사용자' } else { '현재 사용자' }
    if (-not (Confirm-Ui "적용 범위: $scopeLabel`nCopilot 정책과 Edge 사이드바 정책을 설정하고 Copilot 앱을 제거합니다.")) { return }
    Invoke-Ui 'Windows Copilot 차단' -SummarizeChanges { Disable-WindowsCopilot; $ui.gridWin.ItemsSource = @(Get-WindowsCopilotStatus) }
})

$ui.btnWinEnable.Add_Click({
    $scopeLabel = if ($ui.rbMachine.IsChecked) { '이 PC의 모든 사용자' } else { '현재 사용자' }
    if (-not (Confirm-Ui "적용 범위: $scopeLabel`nWindows Copilot 차단 정책을 해제합니다. 제거된 앱은 별도로 설치해야 합니다.")) { return }
    Invoke-Ui 'Windows Copilot 해제' -SummarizeChanges { Enable-WindowsCopilot; $ui.gridWin.ItemsSource = @(Get-WindowsCopilotStatus) }
})
#endregion

Write-UiLog "준비 완료. 시뮬레이션 모드가 켜져 있습니다. 실제 적용하려면 하단 체크를 해제하세요."
$win.ShowDialog() | Out-Null
