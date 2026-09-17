[CmdletBinding()]
param(
    [ValidateSet("CPU", "CUDA")][string]$Lane = "CPU",
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')][string]$OutputTag,
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
if ($OutputTag) { $releaseRoot = "$releaseRoot-$OutputTag" }
$packageDir = Join-Path $releaseRoot "SDRNativeMonitoring"
$buildRoot = Join-Path $repoRoot ("build\sdr-release-" + $Lane)
if ($OutputTag) { $buildRoot = "$buildRoot-$OutputTag" }
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

# Only a full pipeline may assert source provenance. Skip modes remain useful
# diagnostics but must never manufacture provenance for pre-existing outputs.
$bindSource = -not ($SkipNative -or $SkipFreeze -or $SkipTests)
$snapshotTool = Join-Path $repoRoot "scripts\sdr_source_snapshot.py"
$sourceSnapshotPath = Join-Path $buildRoot "source_inputs.json"
if ($bindSource) {
    & $python $snapshotTool --root $repoRoot --output $sourceSnapshotPath
    if ($LASTEXITCODE -ne 0) { throw "source capture failed" }
}

if (-not $SkipNative) {
    & (Join-Path $repoRoot "build_native_sdr.ps1") -Configuration Release -Lane $Lane -PythonExecutable $python -SkipTests:$SkipTests
    if ($LASTEXITCODE -ne 0) { throw "native $Lane build failed" }
}
if ($bindSource) {
    & $python $snapshotTool --root $repoRoot --verify $sourceSnapshotPath
    if ($LASTEXITCODE -ne 0) { throw "source changed during native build" }
}
$nativeModules = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "sdr_monitor") -Filter "_sdr_native*.pyd" -File)
if ($nativeModules.Count -ne 1) { throw "Expected one ABI-specific _sdr_native extension before freeze, found $($nativeModules.Count)" }
$freezeNativeHash = (Get-FileHash -LiteralPath $nativeModules[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
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
    # Codex helper runtimes can put Poppler/ICU and libheif DLL directories on
    # PATH. They are not product dependencies: collecting their ICU shadows
    # Windows ICU and breaks Qt's unversioned ucnv_* imports. Isolate only the
    # freeze child; preserve the caller's environment and explicit libiio input.
    $freezeOriginalPath = $env:PATH
    try {
        $env:PATH = ($freezeOriginalPath.Split(';') | Where-Object { $_ -notmatch '[\\/]codex-runtimes[\\/]' }) -join ';'
        # Preserve stdout for --version and frozen metadata/smoke commands.
        # Hide only a console owned by this GUI launch, never a caller's shell.
        & $python -m PyInstaller --noconfirm --clean --onedir --hide-console hide-early --name SDRNativeMonitoring --distpath $releaseRoot --workpath $buildRoot --specpath $buildRoot --exclude-module esw_dfl --exclude-module olefile --exclude-module _sgram_native --hidden-import sdr_monitor.main --add-binary ("$($nativeModules[0].FullName);sdr_monitor") @libiioPyInstallerArgs (Join-Path $repoRoot "main_sdr.py")
    } finally {
        $env:PATH = $freezeOriginalPath
    }
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller standalone freeze failed" }
}
if (-not (Test-Path -LiteralPath (Join-Path $packageDir "SDRNativeMonitoring.exe"))) { throw "standalone executable was not produced" }
if ($bindSource) {
    & $python $snapshotTool --root $repoRoot --verify $sourceSnapshotPath
    if ($LASTEXITCODE -ne 0) { throw "source changed during freeze" }
    $packagedNative = @(Get-ChildItem -LiteralPath $packageDir -Recurse -File -Filter '_sdr_native*.pyd')
    if ($packagedNative.Count -ne 1) { throw "package must contain exactly one native artifact" }
    if ((Get-FileHash -LiteralPath $packagedNative[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $freezeNativeHash) {
        throw "packaged native artifact differs from the native build output"
    }
    Copy-Item -LiteralPath $sourceSnapshotPath -Destination (Join-Path $packageDir 'source_inputs.json')
    $provenance = [ordered]@{
        schema = 'sdr-pipeline-provenance-v1'
        evidence_kind = 'pipeline-bound-not-binary-attested'
        source_sha256 = (Get-Content -LiteralPath $sourceSnapshotPath -Raw | ConvertFrom-Json).source_sha256
        native_sha256 = $freezeNativeHash
        lane = $Lane
        native_build_and_tests_executed = $true
        source_verified_after_native_and_freeze = $true
    }
    $provenance | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $packageDir 'build_provenance.json') -Encoding UTF8
} else {
    Write-Warning 'Diagnostic skip mode: current-source provenance is not asserted.'
}
$preflight = Join-Path $repoRoot "scripts\preflight_sdr_release.py"
$version = (& $python -c "from sdr_monitor._version import __version__; print(__version__)").Trim()
& $python $preflight --dist-dir $packageDir --manifest (Join-Path $packageDir "release_manifest.json") --lane $Lane --version $version
if ($LASTEXITCODE -ne 0) { throw "standalone release manifest generation failed" }
$manifestPath = Join-Path $packageDir "release_manifest.json"
& $python $preflight --dist-dir $packageDir --manifest $manifestPath --lane $Lane --version $version --verify-existing
if ($LASTEXITCODE -ne 0) { throw "standalone release manifest verification failed" }
$frozenVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_package.py"
& $python $frozenVerifier --package-dir $packageDir --manifest $manifestPath --lane $Lane --version $version
if ($LASTEXITCODE -ne 0) { throw "standalone frozen native-artifact verification failed" }
$offscreenShellVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_shell.py"
& $python $offscreenShellVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen offscreen AppShell verification failed" }
$defaultOffscreenShellVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_default_shell.py"
& $python $defaultOffscreenShellVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen default-composition AppShell verification failed" }
$libiioRuntimeVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_libiio_runtime.py"
& $python $libiioRuntimeVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen libiio runtime-loader verification failed" }
$tinysaRuntimeVerifier = Join-Path $repoRoot "scripts\verify_sdr_frozen_tinysa_runtime.py"
& $python $tinysaRuntimeVerifier --package-dir $packageDir
if ($LASTEXITCODE -ne 0) { throw "standalone frozen tinySA UI/serial runtime verification failed" }
Write-Host "SDR Native Monitoring $Lane release ready: $packageDir"
