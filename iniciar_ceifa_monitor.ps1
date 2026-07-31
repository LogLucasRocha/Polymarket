$ErrorActionPreference = "Stop"
$monitorRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorUrl = "http://localhost:8765"
$dataUpdated = $false

# O lago é consolidado no GitHub depois que o dia UTC fecha. Atualizamos apenas
# os arquivos de dados; alterações locais no código do painel não bloqueiam nem
# são sobrescritas.
try {
    $gitExe = (Get-Command git -ErrorAction Stop).Source
    & $gitExe -C $monitorRoot fetch origin --quiet 2>$null
    if ($LASTEXITCODE -eq 0) {
        & $gitExe -C $monitorRoot diff --quiet origin/main -- dados dados_low
        if ($LASTEXITCODE -eq 1) {
            & $gitExe -C $monitorRoot restore --source=origin/main --worktree -- `
                dados dados_low 2>$null
            $dataUpdated = $LASTEXITCODE -eq 0
        }
    }
} catch {
    # Atualização automática é acessória; a abertura local continua abaixo.
}

# O Streamlit mantém o cálculo em cache. Se chegaram partições novas, reinicia
# somente o processo deste monitor para que os indicadores sejam recalculados.
if ($dataUpdated) {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -match "streamlit.+ceifa_monitor\.py"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

try {
    Invoke-WebRequest -Uri "$monitorUrl/_stcore/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source
    $arguments = @(
        "-m", "streamlit", "run", "ceifa_monitor.py",
        "--server.port", "8765",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    )
    Start-Process -FilePath $pythonExe -ArgumentList $arguments `
        -WorkingDirectory $monitorRoot -WindowStyle Hidden

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest -Uri "$monitorUrl/_stcore/health" `
                -UseBasicParsing -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            # O Streamlit ainda está iniciando.
        }
    }
    if (-not $ready) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "O Monitor Ceifa não iniciou. Tente novamente em alguns segundos.",
            "Monitor Ceifa"
        ) | Out-Null
        exit 1
    }
}

Start-Process $monitorUrl
