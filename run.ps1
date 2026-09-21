$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw '未找到 Python。请安装 Python 3.11+ 并勾选 Add Python to PATH。'
}
py -m pip install -r requirements.txt
py app.py
