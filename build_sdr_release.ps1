[CmdletBinding()]
param(
    [ValidateSet("CPU", "CUDA")][string]$Lane = "CPU",
    [switch]$SkipNative,
    [switch]$SkipFreeze,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = if ($env:SDR_PYTHON_EXECUTABLE) { $env:SDR_PYTHON_EXECUTABLE } else { (& py -3.13 -c "import sys; print(sys.executable)").Trim() }
if (-not $python -or -not (Test-Path -LiteralPath $python)) { throw "Python 3.13 was not resolved" }
$pythonVersion = (& $python -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))').Trim()
if ($pythonVersion -ne "3.13") { throw "S12 requires frozen Python 3.13 ABI, got $pythonVersion" }

$releaseRoot = Join-Path $repoRoot ("dist\SDRNativeMonitoring-" + $Lane)
$packageDir = Join-Path $releaseRoot "SDRNativeMonitoring"
$buildRoot = Join-Path $repoRoot ("build\sdr-release-" + $Lane)
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

if (-not $SkipNative) {
    & (Join-Path $repoRoot "build_native_sdr.ps1") -Configuration Release -Lane $Lane -PythonExecutable $python -SkipTests:$SkipTests
    if ($LASTEXITCODE -ne 0) { throw "native $Lane build failed" }
}
$nativeModules = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "sdr_monitor") -Filter "_sdr_native*.pyd" -File)
if ($nativeModules.Count -ne 1) { throw "Expected one ABI-specific _sdr_native extension before freeze, found $($nativeModules.Count)" }
if (-not $SkipFreeze) {
    & $python -m PyInstaller --noconfirm --clean --onedir --name SDRNativeMonitoring --distpath $releaseRoot --workpath $buildRoot --specpath $buildRoot --exclude-module esw_dfl --exclude-module olefile --exclude-module _sgram_native --hidden-import sdr_monitor.main --add-binary ("$($nativeModules[0].FullName);sdr_monitor") (Join-Path $repoRoot "main_sdr.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller standalone freeze failed" }
}
if (-not (Test-Path -LiteralPath (Join-Path $packageDir "SDRNativeMonitoring.exe"))) { throw "standalone executable was not produced" }
$preflight = Join-Path $repoRoot "scripts\preflight_sdr_release.py"
$version = (& $python -c "from sdr_monitor._version import __version__; print(__version__)").Trim()
& $python $preflight --dist-dir $packageDir --manifest (Join-Path $packageDir "release_manifest.json") --lane $Lane --version $version
Write-Host "SDR Native Monitoring $Lane release ready: $packageDir"
