# Builds dist\LogScope.exe (single file, no console window).   Run from anywhere:  .\packaging\build_exe_onefile.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync                                                    # .venv with PySide6, numpy and the project itself
uv run python -m unittest discover -s tests                # stop here if the core tests fail
uv run --with pyinstaller pyinstaller packaging/logscope_onefile.spec --noconfirm --clean

$zip = "dist\LogScope-0.1.0-win64-onefile.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\LogScope.exe" -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Done: $(Resolve-Path dist\LogScope.exe)"
Write-Host "Zip:   $(Resolve-Path $zip)"
Write-Host "Single-file build: keep the .exe anywhere; runtime unpacks to a temp folder."