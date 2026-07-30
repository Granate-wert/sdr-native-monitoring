[CmdletBinding()]
param(
    [ValidateSet("disable", "force")]
    [string]$ConsoleMode = "disable",
    [string]$OutputDirectory = "build\\nuitka_release_full_20260722_ps2"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python runtime was not found: $python"
}

$basePrefix = (& $python -c "import sys; print(sys.base_prefix)").Trim()
$pythonDll = Join-Path $basePrefix "python312.dll"
if (-not (Test-Path -LiteralPath $pythonDll)) {
    throw "Python runtime DLL was not found: $pythonDll"
}

$nativeDecoder = Get-ChildItem -LiteralPath (Join-Path $root "esw_dfl") -Filter "_sgram_native*.pyd" |
    Select-Object -First 1
if ($null -eq $nativeDecoder) {
    throw "Native SgramLine decoder was not found. Run build_native_decoder.bat first."
}

$matplotlibData = (& $python -c "import matplotlib; from pathlib import Path; print(Path(matplotlib.__file__).parent / 'mpl-data')").Trim()
if (-not (Test-Path -LiteralPath $matplotlibData)) {
    throw "Matplotlib runtime data directory was not found: $matplotlibData"
}
$pyqtgraphMaps = (& $python -c "import pyqtgraph; from pathlib import Path; print(Path(pyqtgraph.__file__).parent / 'colors' / 'maps')").Trim()
if (-not (Test-Path -LiteralPath $pyqtgraphMaps)) {
    throw "PyQtGraph colour-map directory was not found: $pyqtgraphMaps"
}

$env:NUITKA_CACHE_DIR = Join-Path $root "build\nuitka_cache"
$log = Join-Path $root ((Split-Path -Leaf $OutputDirectory) + ".log")

$nuitkaArguments = @(
    "-m", "nuitka",
    "--standalone",
    "--assume-yes-for-downloads",
    "--windows-console-mode=$ConsoleMode",
    "--enable-plugin=pyside6",
    "--include-package-data=imageio_ffmpeg",
    "--include-package-data=matplotlib",
    "--include-data-dir=$matplotlibData=matplotlib/mpl-data",
    "--include-module=esw_dfl._sgram_native",
    "--include-data-file=$pythonDll=python312.dll",
    "--include-data-file=$($nativeDecoder.FullName)=esw_dfl\$($nativeDecoder.Name)",
    "--output-dir=$outputDirectory",
    "--output-filename=RS_DFL_Analyzer.exe",
    "main.py"
)

& $python @nuitkaArguments 2>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$distDirectory = Join-Path $root "$outputDirectory\main.dist"
$runtimeMatplotlibData = Join-Path $distDirectory "matplotlib\mpl-data"
New-Item -ItemType Directory -Force -Path $runtimeMatplotlibData | Out-Null
Get-ChildItem -LiteralPath $matplotlibData -Force | Copy-Item -Destination $runtimeMatplotlibData -Recurse -Force
$runtimePyqtgraphMaps = Join-Path $distDirectory "pyqtgraph\colors\maps"
New-Item -ItemType Directory -Force -Path $runtimePyqtgraphMaps | Out-Null
Get-ChildItem -LiteralPath $pyqtgraphMaps -Force | Copy-Item -Destination $runtimePyqtgraphMaps -Recurse -Force

Write-Host "Built: $(Join-Path $distDirectory "RS_DFL_Analyzer.exe")"
