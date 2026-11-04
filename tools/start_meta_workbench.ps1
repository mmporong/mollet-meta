param([ValidateRange(1024,65535)][int]$Port = 7706)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$stateRoot = Join-Path $env:LOCALAPPDATA 'Mollet/meta-model-api'
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
$statusUrl = "http://127.0.0.1:$Port/api/meta/status"
$alreadyRunning = $null
try { $alreadyRunning = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2 } catch { }
if ($null -ne $alreadyRunning) {
    if ($null -eq $alreadyRunning.status.live_requests_started) { throw '포트를 다른 서비스가 사용 중입니다.' }
    [ordered]@{ url="http://127.0.0.1:$Port"; reused=$true; key_configured=$alreadyRunning.status.key_configured; credential_saved=$alreadyRunning.status.credential_saved } | ConvertTo-Json
    exit 0
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw '포트를 다른 서비스가 사용 중입니다.' }
$pythonCommand = (Get-Command python -ErrorAction Stop).Source
$pythonWindowless = Join-Path (Split-Path -Parent $pythonCommand) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw '설치된 Python의 pythonw.exe를 찾지 못했습니다.' }
$runTag = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,8))
$outLog = Join-Path $stateRoot "workbench-$runTag.out.log"
$errorLog = Join-Path $stateRoot "workbench-$runTag.err.log"
$launchArgs = @('-m','rl_triage.behavior_authoring','serve','--port',"$Port",'--enable-video-calls','--video-input-sha256','e199c6cd71d62894c98a79315b588a74f5290236a706bf7c1368a5568e59a6f0')
# 크레딧 확인과 예산은 저장한 사용자 설정에서만 복원한다.
$previousPythonPath = $env:PYTHONPATH
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONPATH = Join-Path $repoRoot 'src'
    $env:PYTHONUTF8 = '1'
    $metaProcess = Start-Process -FilePath $pythonWindowless -ArgumentList $launchArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errorLog -PassThru
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONUTF8 = $previousUtf8
}
$readyStatus = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { $readyStatus = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 200 }
}
$record = [ordered]@{ url="http://127.0.0.1:$Port"; pid=$metaProcess.Id; source_root=$repoRoot; windowless=$true; stdout=$outLog; stderr=$errorLog; ready=($null -ne $readyStatus); key_configured=($readyStatus.status.key_configured); credential_saved=($readyStatus.status.credential_saved); keep_reason='기존 Meta 연결의 지속 사용과 서버 재시작 시 암호화 설정 복원'; stop_command="Stop-Process -Id $($metaProcess.Id)" }
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateRoot 'workbench-runtime.json') -Encoding utf8
$record | ConvertTo-Json
if ($null -eq $readyStatus) { throw '서버 준비 상태를 확인하지 못했습니다. 실행 기록과 로그를 보존했습니다.' }
