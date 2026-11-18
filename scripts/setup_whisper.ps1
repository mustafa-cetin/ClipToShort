# Downloads whisper.cpp (CUDA + CPU builds), Whisper models and the Silero VAD model into tools\whisper.
#   large-v3       : en dogru (Turkce icin onerilen), ~3.1 GB
#   large-v3-turbo : hizli, daha az dogru, ~1.6 GB
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1 [-Models ggml-large-v3.bin,ggml-large-v3-turbo.bin]
param(
    [string[]]$Models = @("ggml-large-v3.bin", "ggml-large-v3-turbo.bin")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $PSScriptRoot
$wdir = Join-Path $root "tools\whisper"
New-Item -ItemType Directory -Force (Join-Path $wdir "models") | Out-Null

Write-Host "whisper.cpp surum bilgisi aliniyor..."
# Tagged releases (v1.x) may have no binaries; prebuilt zips live on "bNNNN" build releases.
$releases = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=30" -Headers @{ "User-Agent" = "tiktok-video-maker" }
$rel = $releases | Where-Object { $_.assets | Where-Object { $_.name -match '^whisper-bin-x64\.zip$' } } | Select-Object -First 1
if (-not $rel) { throw "Windows paketi olan bir whisper.cpp surumu bulunamadi." }
Write-Host "Surum: $($rel.tag_name)"

function Install-Build($pattern, $target) {
    $asset = $rel.assets | Where-Object { $_.name -match $pattern } | Sort-Object name -Descending | Select-Object -First 1
    if (-not $asset) { Write-Warning "Paket bulunamadi: $pattern"; return }
    $dest = Join-Path $wdir $target
    if (Get-ChildItem $dest -Recurse -Filter whisper-cli.exe -ErrorAction SilentlyContinue) {
        Write-Host "$target zaten kurulu, atlaniyor."; return
    }
    $zip = Join-Path $env:TEMP $asset.name
    Write-Host "Indiriliyor: $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    New-Item -ItemType Directory -Force $dest | Out-Null
    Expand-Archive $zip -DestinationPath $dest -Force
    Remove-Item $zip
    Write-Host "$target kuruldu."
}

# NVIDIA GPU varsa CUDA (cuBLAS 12) paketi, her durumda CPU paketi yedek olarak.
Install-Build '^(whisper-bin-win-cuda-12[\d.]*-x64|whisper-cublas-12[\d.]*-bin-x64)\.zip$' "cuda"
Install-Build '^whisper-bin-x64\.zip$' "cpu"

function Get-Model($name, $url) {
    $path = Join-Path $wdir "models\$name"
    if (Test-Path $path) { Write-Host "Model zaten var: $name"; return }
    Write-Host "Model indiriliyor: $name (buyuk dosyalar biraz surebilir)..."
    $tmp = "$path.part"
    Invoke-WebRequest $url -OutFile $tmp
    Move-Item $tmp $path -Force
}

foreach ($m in $Models) {
    Get-Model $m "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$m"
}
# Silero VAD: konusma olmayan bolumleri atlar (muzik/oyun sesinde uydurma metni engeller).
Get-Model "ggml-silero-v5.1.2.bin" "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
Write-Host ""
Write-Host "Tamam. whisper.cpp hazir: $wdir"
