[CmdletBinding()]
param(
    [ValidateSet("Release", "Debug")][string]$Configuration = "Release",
    [ValidateSet("CPU", "CUDA")][string]$Lane = "CPU",
    [string]$PythonExecutable = "",
    [switch]$Clean,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceDir = Join-Path $repoRoot "native\sdr_core"
$outDir = Join-Path $sourceDir "out"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Import-MsvcEnvironment {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) {
        throw "vswhere.exe was not found; install Visual Studio Build Tools with C++ workload"
    }
    $installationPath = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    if (-not $installationPath) {
        throw "MSVC x64 build tools were not found"
    }
    $devCmd = Join-Path $installationPath "Common7\Tools\VsDevCmd.bat"
    $environmentLines = & $env:ComSpec /d /s /c "`"$devCmd`" -no_logo -arch=x64 -host_arch=x64 && set"
    if ($LASTEXITCODE -ne 0) {
        throw "VsDevCmd.bat failed with exit code $LASTEXITCODE"
    }
    foreach ($line in $environmentLines) {
        if ($line -match "^([^=]+)=(.*)$") {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
    return $installationPath
}

if (-not $PythonExecutable) {
    $repositoryPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repositoryPython) {
        $PythonExecutable = $repositoryPython
    } elseif ($env:SDR_PYTHON_EXECUTABLE -and (Test-Path -LiteralPath $env:SDR_PYTHON_EXECUTABLE)) {
        $PythonExecutable = $env:SDR_PYTHON_EXECUTABLE
    } else {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $PythonExecutable = (& $launcher.Source -3.13 -c "import sys; print(sys.executable)").Trim()
        }
    }
}
if (-not $PythonExecutable -or -not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Python was not resolved. Pass -PythonExecutable or create .venv."
}
$pythonVersion = (& $PythonExecutable -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))').Trim()
if ($pythonVersion -ne "3.13") { throw "S12 requires frozen Python 3.13 ABI, got $pythonVersion" }
if ($Clean) {
    Get-ChildItem -LiteralPath (Join-Path $repoRoot "sdr_monitor") -Filter "_sdr_native*.pyd" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    Get-ChildItem -LiteralPath (Join-Path $repoRoot "sdr_monitor") -Filter "_sdr_native*.pdb" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    foreach ($generatedDir in @("build", "python", "scikit-build")) {
        $target = Join-Path $outDir $generatedDir
        if (-not (Test-Path -LiteralPath $target)) {
            continue
        }
        $resolvedSource = [System.IO.Path]::GetFullPath($sourceDir)
        $resolvedTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $resolvedTarget.StartsWith($resolvedSource, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean outside native/sdr_core: $resolvedTarget"
        }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

$vsInstall = Import-MsvcEnvironment
$env:VSLANG = '1033'
$cmake = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$ctest = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe"
$ninjaDir = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
if (-not (Test-Path -LiteralPath $cmake) -or -not (Test-Path -LiteralPath $ctest)) {
    throw "Visual Studio CMake tools were not found under $vsInstall"
}
$env:PATH = "$ninjaDir;$env:PATH"

# Probe actual output instead of assuming that the optional English MSVC
# language pack exists. /EP only preprocesses this checked-in source; it
# creates no object/output file and performs no device or network operation.
$probeOutputEncoding = [Console]::OutputEncoding
try {
    # MSVC emits localized diagnostics in the Windows ANSI code page even
    # when this PowerShell host decodes native stdout as UTF-8. Decode only
    # this child with the actual system ACP; restore the host immediately.
    $systemAnsiPage = [int](Get-ItemProperty 'HKLM:/SYSTEM/CurrentControlSet/Control/Nls/CodePage' -Name ACP).ACP
    [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding($systemAnsiPage)
    $includeProbe = & cl.exe /nologo /showIncludes /EP /TP "/I$sourceDir/include" "$sourceDir/src/core/api.cpp" 2>&1
    $includeProbeExit = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $probeOutputEncoding
}
if ($includeProbeExit -ne 0) { throw 'MSVC header-dependency probe failed' }
$includeProbeLine = $includeProbe | ForEach-Object { $_.ToString() } |
    Where-Object { $_ -match '[\\/]sdr_core[\\/]api\.hpp$' } | Select-Object -First 1
if (-not $includeProbeLine -or $includeProbeLine -notmatch '^(.+?)([A-Za-z]:[\\/])') {
    throw 'MSVC /showIncludes prefix could not be verified from the actual compiler output'
}
$dependencyPrefix = $Matches[1]
if ($dependencyPrefix.Contains([char]0xfffd)) { throw 'MSVC dependency prefix contains undecodable bytes' }
Write-Host "Verified MSVC /showIncludes prefix: $dependencyPrefix"

$localPybind = Join-Path $sourceDir "out\python-tools\pybind11\share\cmake\pybind11"
if (Test-Path -LiteralPath $localPybind) {
    $pybindCmakeDir = $localPybind
} else {
    $pybindCmakeDir = (& $PythonExecutable -c "import pybind11; print(pybind11.get_cmake_dir())").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $pybindCmakeDir) {
        throw "pybind11 was not found. Install it or populate native/sdr_core/out/python-tools."
    }
}

$env:SDR_PYTHON_EXECUTABLE = [System.IO.Path]::GetFullPath($PythonExecutable)
$env:SDR_PYBIND11_CMAKE_DIR = [System.IO.Path]::GetFullPath($pybindCmakeDir)

if ($Lane -eq "CUDA") {
    if ($Configuration -eq "Debug") { throw "CUDA lane supports Release only" }
    $configurePreset = "windows-msvc-cuda"
    $buildPreset = "windows-msvc-cuda-release"
    $testPreset = "windows-msvc-cuda"
    $artifactDir = Join-Path $sourceDir "out\build\windows-msvc-cuda\python"
} elseif ($Configuration -eq "Debug") {
    $configurePreset = "windows-msvc-cpu-debug"
    $buildPreset = "windows-msvc-cpu-debug"
    $testPreset = "windows-msvc-cpu-debug"
    $artifactDir = Join-Path $sourceDir "out\python\debug"
} else {
    $configurePreset = "windows-msvc-cpu"
    $buildPreset = "windows-msvc-cpu-release"
    $testPreset = "windows-msvc-cpu"
    $artifactDir = Join-Path $sourceDir "out\build\windows-msvc-cpu\python"
}


Push-Location $sourceDir
try {
    Invoke-Checked -FilePath $cmake -Arguments @("--preset", $configurePreset, "-DSDR_CORE_PYTHON_OUTPUT_DIR=$artifactDir", "-DSDR_MSVC_SHOWINCLUDES_PREFIX=$dependencyPrefix")
    $nativeBuildDir = Join-Path $sourceDir "out/build/$configurePreset"
    $ninja = Join-Path $ninjaDir 'ninja.exe'
    $dependencyObject = 'CMakeFiles/sdr_core.dir/src/core/sweep_line_assembler.cpp.obj'
    $dependencyPattern = 'include[\\/]sdr_core[\\/]types\.hpp'
    $buildArguments = @('--build', '--preset', $buildPreset)
    if (Test-Path -LiteralPath (Join-Path $nativeBuildDir $dependencyObject)) {
        $existingDependencies = (& $ninja -C $nativeBuildDir -t deps $dependencyObject) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $existingDependencies -notmatch $dependencyPattern) {
            Write-Warning 'Native header dependencies missing: rebuilding generated objects before acceptance.'
            $buildArguments += '--clean-first'
        }
    }
    Invoke-Checked -FilePath $cmake -Arguments $buildArguments
    $verifiedDependencies = (& $ninja -C $nativeBuildDir -t deps $dependencyObject) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $verifiedDependencies -notmatch $dependencyPattern) {
        throw 'Native header dependency verification failed; refusing to activate or package a stale-ABI build.'
    }
    Write-Host 'Native header dependency verification PASS (sweep_line_assembler -> types.hpp)'
    if (-not $SkipTests) {
        Invoke-Checked -FilePath $ctest -Arguments @("--preset", $testPreset)
    }
} finally {
    Pop-Location
}

$extensionSuffix = (& $PythonExecutable -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))').Trim()
if (-not $extensionSuffix) {
    throw "Python extension suffix could not be resolved for $PythonExecutable"
}
$expectedArtifactName = "_sdr_native$extensionSuffix"
$artifacts = @(Get-ChildItem -LiteralPath $artifactDir -Filter $expectedArtifactName -File)
if ($artifacts.Count -ne 1) {
    throw "Expected one $expectedArtifactName in $artifactDir, found $($artifacts.Count)"
}
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
$pythonAbi = ($artifacts[0].BaseName -replace "^_sdr_native\.", "")
$artifactSha256 = (Get-FileHash -LiteralPath $artifacts[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    preset = $configurePreset
    cuda_compiled = ($Lane -eq "CUDA")
    python_abi = $pythonAbi
    native_version = "0.6.0"
    source_commit = $sourceCommit
    artifact_sha256 = $artifactSha256
}
$manifestPath = Join-Path $artifactDir "native_build_manifest.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$preflight = Join-Path $repoRoot "scripts\preflight_sdr_native_build.py"
$preflightArgs = @($preflight, "--module", $artifacts[0].FullName, "--manifest", $manifestPath)
if ($Lane -eq "CUDA") { $preflightArgs += "--expect-cuda" } else { $preflightArgs += "--expect-cpu" }
Invoke-Checked -FilePath $PythonExecutable -Arguments $preflightArgs
if ($Configuration -eq "Release") {
    $activeDir = Join-Path $repoRoot "sdr_monitor"
    # Keep only the extension matching the selected Python ABI in the active
    # standalone package. Older ABI artifacts must not shadow a rerun.
    Get-ChildItem -LiteralPath $activeDir -Filter "_sdr_native*.pyd" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne $artifacts[0].Name } |
        Remove-Item -Force
    $active = Join-Path $activeDir $artifacts[0].Name
    $part = "$active.part"
    Copy-Item -LiteralPath $artifacts[0].FullName -Destination $part -Force
    Move-Item -LiteralPath $part -Destination $active -Force
    $activeManifest = Join-Path $activeDir "native_build_manifest.json"
    Copy-Item -LiteralPath $manifestPath -Destination $activeManifest -Force
    # The staged module passed its own preflight above. Verify that the
    # atomically installed application copy and its manifest are byte-for-byte
    # identical before a release build reports success.
    $activePreflightArgs = @(
        $preflight,
        "--module", $artifacts[0].FullName,
        "--manifest", $manifestPath,
        "--active-module", $active,
        "--active-manifest", $activeManifest
    )
    if ($Lane -eq "CUDA") { $activePreflightArgs += "--expect-cuda" } else { $activePreflightArgs += "--expect-cpu" }
    Invoke-Checked -FilePath $PythonExecutable -Arguments $activePreflightArgs
    # A separate isolated interpreter proves that the next process imports this
    # active extension through the one canonical pybind identity, rather than
    # only comparing files in the build process that just activated it.
    $activeImportVerifier = Join-Path $repoRoot "scripts\verify_sdr_native_active_import.py"
    Invoke-Checked -FilePath $PythonExecutable -Arguments @(
        "-I",
        $activeImportVerifier,
        "--module", $active,
        "--manifest", $activeManifest
    )
}
Write-Host "S12 native module ($Lane/$Configuration): $($artifacts[0].FullName)"
