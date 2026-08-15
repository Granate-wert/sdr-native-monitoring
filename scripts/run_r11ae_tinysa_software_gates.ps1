<#
.SYNOPSIS
Runs the R11-AE software-only admission matrix with an explicitly supplied
Python 3.13 environment and a freshly built frozen package.

.DESCRIPTION
This script never enables the R11-AE visible executor and never opens a tinySA
port.  It pins Qt to offscreen mode, disables automatic discovery and verifies
only import, fake/offscreen tests, static analysis and the package's inert
tinySA runtime command.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$PythonExecutable,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$PackageDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-R11AEPython {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & $script:ResolvedPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "R11-AE software gate failed: python $($Arguments -join ' ')"
    }
}

$ResolvedPython = (Resolve-Path -LiteralPath $PythonExecutable).Path
$ResolvedPackage = (Resolve-Path -LiteralPath $PackageDirectory).Path

$pythonVersion = & $ResolvedPython -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne '3.13') {
    throw 'R11-AE software gates require the declared Python 3.13 environment'
}

$savedAutoDiscover = $env:SDR_AUTO_DISCOVER
$savedQtPlatform = $env:QT_QPA_PLATFORM
try {
    # These gates admit neither normal discovery nor visible/DPI evidence.
    $env:SDR_AUTO_DISCOVER = '0'
    $env:QT_QPA_PLATFORM = 'offscreen'

    Invoke-R11AEPython -Arguments @('-c', 'import PySide6, serial; print("R11-AE dependencies available")')
    Invoke-R11AEPython -Arguments @(
        '-m', 'unittest',
        'tests.test_r11ae_tinysa_visible_ui_evidence',
        'tests.test_r11ae_tinysa_frozen_runtime',
        'tests.test_r11ae_governance',
        'tests.test_r11ae_tinysa_offscreen_ui'
    )
    Invoke-R11AEPython -Arguments @(
        '-m', 'ruff', 'check',
        'sdr_monitor/r11ae_tinysa_visible_ui_evidence.py',
        'sdr_monitor/application/tinysa_analyzer.py',
        'sdr_monitor/ui/tinysa_trace_canvas.py',
        'sdr_monitor/ui/presenters/tinysa_analyzer_presenter.py',
        'sdr_monitor/ui/presenters/tinysa_source_activation_presenter.py',
        'sdr_monitor/ui/dialogs/tinysa_source_activation.py',
        'sdr_monitor/ui/app_shell.py',
        'sdr_monitor/ui/workspaces/tinysa_analyzer.py',
        'sdr_monitor/ui/r11ae_tinysa_visible_witness.py',
        'scripts/r11ae_tinysa_visible_ui.py',
        'tests/test_r11ae_tinysa_visible_ui_evidence.py',
        'tests/test_r11ae_tinysa_offscreen_ui.py'
    )
    Invoke-R11AEPython -Arguments @(
        '-m', 'mypy',
        'sdr_monitor/r11ae_tinysa_visible_ui_evidence.py',
        'sdr_monitor/application/tinysa_analyzer.py',
        'sdr_monitor/ui/tinysa_trace_canvas.py',
        'sdr_monitor/ui/presenters/tinysa_analyzer_presenter.py',
        'sdr_monitor/ui/presenters/tinysa_source_activation_presenter.py',
        'sdr_monitor/ui/dialogs/tinysa_source_activation.py',
        'sdr_monitor/ui/workspaces/tinysa_analyzer.py',
        'sdr_monitor/ui/r11ae_tinysa_visible_witness.py',
        'scripts/r11ae_tinysa_visible_ui.py'
    )
    Invoke-R11AEPython -Arguments @(
        'scripts/verify_sdr_frozen_tinysa_runtime.py',
        '--package-dir', $ResolvedPackage
    )
}
finally {
    $env:SDR_AUTO_DISCOVER = $savedAutoDiscover
    $env:QT_QPA_PLATFORM = $savedQtPlatform
}

Write-Output 'R11-AE software-only admission gates passed; visible execution remains disabled.'
