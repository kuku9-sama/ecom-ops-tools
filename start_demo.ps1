param([switch]$NoBrowser)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "没找到 .venv。先按 README 的安装步骤建好虚拟环境。" -ForegroundColor Red
    exit 1
}

function Get-FreePort {
    foreach ($candidate in @(8000, 8010) + (8001..8009)) {
        try {
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $candidate)
            $listener.Start()
            $listener.Stop()
            return $candidate
        } catch {
            continue
        }
    }
    return $null
}

Write-Host "== 1/2 运行完整演示 ==" -ForegroundColor Cyan
& $python "scripts/demo.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "演示失败，退出码 $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$port = Get-FreePort
if ($null -eq $port) {
    Write-Host "8000-8010 端口都被占用了。" -ForegroundColor Red
    exit 1
}

$env:ECOMMERCE_DB = "data/demo_run.db"
$url = "http://127.0.0.1:$port/"

Write-Host ""
Write-Host "== 2/2 启动看板 ==" -ForegroundColor Cyan
Write-Host "地址：$url"
Write-Host "按 Ctrl+C 停止服务。" -ForegroundColor DarkGray

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($target, $targetPort)
        for ($i = 0; $i -lt 40; $i++) {
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $client.Connect("127.0.0.1", $targetPort)
                $client.Close()
                Start-Process $target
                return
            } catch {
                Start-Sleep -Milliseconds 250
            }
        }
    } -ArgumentList $url, $port | Out-Null
}

& $python -m ecommerce.cli serve --port $port
