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
$cmake = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$ctest = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\ctest.exe"
$ninjaDir = Join-Path $vsInstall "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
if (-not (Test-Path -LiteralPath $cmake) -or -not (Test-Path -LiteralPath $ctest)) {
    throw "Visual Studio CMake tools were not found under $vsInstall"
}
$env:PATH = "$ninjaDir;$env:PATH"

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
    Invoke-Checked -FilePath $cmake -Arguments @("--preset", $configurePreset, "-DSDR_CORE_PYTHON_OUTPUT_DIR=$artifactDir")
    Invoke-Checked -FilePath $cmake -Arguments @("--build", "--preset", $buildPreset)
    if (-not $SkipTests) {
        Invoke-Checked -FilePath $ctest -Arguments @("--preset", $testPreset)
    }
} finally {
    Pop-Location
}

$artifacts = @(Get-ChildItem -LiteralPath $artifactDir -Filter "_sdr_native*.pyd" -File)
if ($artifacts.Count -ne 1) {
    throw "Expected one _sdr_native extension in $artifactDir, found $($artifacts.Count)"
}
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
$pythonAbi = ($artifacts[0].BaseName -replace "^_sdr_native\.", "")
$manifest = [ordered]@{
    preset = $configurePreset
    cuda_compiled = ($Lane -eq "CUDA")
    python_abi = $pythonAbi
    native_version = "0.6.0"
    source_commit = $sourceCommit
}
$manifestPath = Join-Path $artifactDir "native_build_manifest.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$preflight = Join-Path $repoRoot "scripts\preflight_sdr_native_build.py"
$preflightArgs = @($preflight, "--module", $artifacts[0].FullName, "--manifest", $manifestPath)
if ($Lane -eq "CUDA") { $preflightArgs += "--expect-cuda" } else { $preflightArgs += "--expect-cpu" }
Invoke-Checked -FilePath $PythonExecutable -Arguments $preflightArgs
if ($Configuration -eq "Release") {
    $activeDir = Join-Path $repoRoot "sdr_monitor"
    $active = Join-Path $activeDir $artifacts[0].Name
    $part = "$active.part"
    Copy-Item -LiteralPath $artifacts[0].FullName -Destination $part -Force
    Move-Item -LiteralPath $part -Destination $active -Force
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $activeDir "native_build_manifest.json") -Force
}
Write-Host "S12 native module ($Lane/$Configuration): $($artifacts[0].FullName)"
