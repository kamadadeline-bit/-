param(
    [string]$FfmpegBin = $env:FFMPEG_BIN,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Find-FfmpegBin {
    param([string]$Requested)
    $candidates = @()
    if ($Requested) { $candidates += $Requested }
    $candidates += @(
        (Join-Path $root 'ffmpeg\bin'),
        (Join-Path $env:USERPROFILE 'Documents\ChatGPT\4号版本去重\portable_build\ffmpeg_extract\ffmpeg-9.0.1-essentials_build\bin'),
        (Join-Path $env:USERPROFILE 'Documents\ChatGPT\4号版本去重\OriginalVideoAuto_Portable_20260825_v6\bin')
    )
    foreach ($candidate in $candidates | Where-Object { $_ }) {
        $full = [System.IO.Path]::GetFullPath($candidate)
        if ((Test-Path (Join-Path $full 'ffmpeg.exe')) -and (Test-Path (Join-Path $full 'ffprobe.exe'))) {
            return $full
        }
    }
    throw '未找到 FFmpeg。请用 -FfmpegBin 指定同时包含 ffmpeg.exe 和 ffprobe.exe 的 bin 文件夹。'
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw '未找到 Python 启动器 py。请安装 Python 3.11+（Windows x64）。'
}

if (-not $SkipInstall) {
    & py -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
}

$ffmpegBin = Find-FfmpegBin $FfmpegBin
$buildRoot = Join-Path $root 'build'
$distRoot = Join-Path $buildRoot 'dist'
$workRoot = Join-Path $buildRoot 'pyinstaller-work'
$specRoot = Join-Path $buildRoot 'spec'
$name = 'KuaishouLiveAssistant'
$packageRoot = Join-Path $distRoot $name
$releaseRoot = Join-Path $root 'release'
$zipPath = Join-Path $releaseRoot ("{0}_Portable_{1}.zip" -f $name, (Get-Date -Format 'yyyyMMdd_HHmmss'))

Remove-Item -LiteralPath $packageRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $specRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $distRoot, $workRoot, $specRoot, $releaseRoot | Out-Null

& py -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name $name `
    --distpath $distRoot `
    --workpath $workRoot `
    --specpath $specRoot `
    app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败。' }

$runtimeBin = Join-Path $packageRoot 'ffmpeg\bin'
New-Item -ItemType Directory -Force -Path $runtimeBin | Out-Null
Get-ChildItem -LiteralPath $ffmpegBin -Force | Copy-Item -Destination $runtimeBin -Recurse -Force
Copy-Item -LiteralPath 'README.md' -Destination (Join-Path $packageRoot 'README.md') -Force
@'
快手合规直播助手

双击 KuaishouLiveAssistant.exe 启动。此目录可以整体复制到其他 Windows x64 电脑。
首次使用前请阅读 README.md，并确认摄像头、素材版权和本场 RTMP 地址均已准备好。
本软件不提供账号登录、自动获取推流码、无人值守挂机或规避平台检测功能。
'@ | Set-Content -LiteralPath (Join-Path $packageRoot '使用说明.txt') -Encoding UTF8

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageRoot "$name.exe")).Hash
"SHA256  $hash  $name.exe" | Set-Content -LiteralPath (Join-Path $packageRoot 'SHA256SUMS.txt') -Encoding ASCII

Compress-Archive -Path (Join-Path $packageRoot '*') -DestinationPath $zipPath -CompressionLevel Optimal -Force
Write-Output "PACKAGE=$packageRoot"
Write-Output "ZIP=$zipPath"
Write-Output "FFMPEG_BIN=$ffmpegBin"
