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
$libiioRuntimeNames = @("libiio.dll", "libserialport-0.dll", "libusb-1.0.dll", "libxml2-2.dll", "libiconv-2.dll", "liblzma-5.dll", "zlib1.dll")
$libiioPyInstallerArgs = @()
if (-not $SkipFreeze) {
    $libiioRuntimeDir = if ($env:SDR_LIBIIO_RUNTIME_DIR) { $env:SDR_LIBIIO_RUNTIME_DIR } else { Join-Path $env:ProgramFiles "IIO Oscilloscope\bin" }
    if (-not (Test-Path -LiteralPath $libiioRuntimeDir -PathType Container)) { throw "R12-I requires a local libiio runtime directory: $libiioRuntimeDir" }
    $libiioRuntimeDir = (Resolve-Path -LiteralPath $libiioRuntimeDir).Path
    foreach ($libiioRuntimeName in $libiioRuntimeNames) {
        $libiioRuntimePath = Join-Path $libiioRuntimeDir $libiioRuntimeName
        if (-not (Test-Path -LiteralPath $libiioRuntimePath -PathType Leaf)) { throw "R12-I requires app-local runtime component: $libiioRuntimePath" }
        $libiioPyInstallerArgs += "--add-binary"
        $libiioPyInstallerArgs += "$libiioRuntimePath;sdr_monitor"
    }
    & $python -m PyInstaller --noconfirm --clean --onedir --name SDRNativeMonitoring --distpath $releaseRoot --workpath $buildRoot --specpath $buildRoot --exclude-module esw_dfl --exclude-module olefile --exclude-module _sgram_native --hidden-import sdr_monitor.main --add-binary ("$($nativeModules[0].FullName);sdr_monitor") @libiioPyInstallerArgs (Join-Path $repoRoot "main_sdr.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller standalone freeze failed" }
}
if (-not (Test-Path -LiteralPath (Join-Path $packageDir "SDRNativeMonitoring.exe"))) { throw "standalone executable was not produced" }
$preflight = Join-Path $repoRoot "scripts\preflight_sdr_release.py"
$version = (& $python -c "from sdr_monitor._version import __version__; print(__version__)").Trim()
& $python $preflight --dist-dir $packageDir --manifest (Join-Path $packageDir "release_manifest.json") --lane $Lane --version $version
if ($LASTEXITCODE -ne 0) { throw "standalone frozen package preflight failed" }
$libiioRuntimeVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_libiio_runtime.py"
& $python $libiioRuntimeVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen libiio runtime-loader verification failed" }
$tinysaRuntimeVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_tinysa_runtime.py"
& $python $tinysaRuntimeVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen tinySA UI/serial runtime verification failed" }
Write-Host "SDR Native Monitoring $Lane release ready: $packageDir"
