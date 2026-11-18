# Downloads whisper.cpp (CUDA + CPU builds), Whisper models and the Silero VAD model into tools\whisper.
#   large-v3       : most accurate (recommended for Turkish), ~3.1 GB
#   large-v3-turbo : fast, less accurate, ~1.6 GB
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

Write-Host "Fetching whisper.cpp release info..."
# Tagged releases (v1.x) may have no binaries; prebuilt zips live on "bNNNN" build releases.
$releases = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=30" -Headers @{ "User-Agent" = "tiktok-video-maker" }
$rel = $releases | Where-Object { $_.assets | Where-Object { $_.name -match '^whisper-bin-x64\.zip$' } } | Select-Object -First 1
if (-not $rel) { throw "No whisper.cpp release with a Windows package was found." }
Write-Host "Release: $($rel.tag_name)"

function Install-Build($pattern, $target) {
    $asset = $rel.assets | Where-Object { $_.name -match $pattern } | Sort-Object name -Descending | Select-Object -First 1
    if (-not $asset) { Write-Warning "Package not found: $pattern"; return }
    $dest = Join-Path $wdir $target
    if (Get-ChildItem $dest -Recurse -Filter whisper-cli.exe -ErrorAction SilentlyContinue) {
        Write-Host "$target already installed, skipping."; return
    }
    $zip = Join-Path $env:TEMP $asset.name
    Write-Host "Downloading: $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    New-Item -ItemType Directory -Force $dest | Out-Null
    Expand-Archive $zip -DestinationPath $dest -Force
    Remove-Item $zip
    Write-Host "$target installed."
}

# CUDA (cuBLAS 12) build for NVIDIA GPUs, plus the CPU build as a fallback in every case.
Install-Build '^(whisper-bin-win-cuda-12[\d.]*-x64|whisper-cublas-12[\d.]*-bin-x64)\.zip$' "cuda"
Install-Build '^whisper-bin-x64\.zip$' "cpu"

function Get-Model($name, $url) {
    $path = Join-Path $wdir "models\$name"
    if (Test-Path $path) { Write-Host "Model already present: $name"; return }
    Write-Host "Downloading model: $name (large files can take a while)..."
    $tmp = "$path.part"
    Invoke-WebRequest $url -OutFile $tmp
    Move-Item $tmp $path -Force
}

foreach ($m in $Models) {
    Get-Model $m "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$m"
}
# Silero VAD: skips non-speech parts (prevents invented text on music/game audio).
Get-Model "ggml-silero-v5.1.2.bin" "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
Write-Host ""
Write-Host "Done. whisper.cpp is ready: $wdir"
