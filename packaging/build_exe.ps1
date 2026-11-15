# Builds dist\LogScope\LogScope.exe (no console window).   Run from anywhere:  .\packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync                                                    # .venv with PySide6, numpy and the project itself
uv run python -m unittest discover -s tests                # stop here if the core tests fail
uv run --with pyinstaller pyinstaller packaging/logscope.spec --noconfirm --clean

$zip = "dist\LogScope-0.1.0-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\LogScope\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Done: $(Resolve-Path dist\LogScope\LogScope.exe)"
Write-Host "Zip:   $(Resolve-Path $zip)"
Write-Host "Copy the whole dist\LogScope folder, or upload the zip to a GitHub Release."
