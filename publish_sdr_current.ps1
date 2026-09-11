[CmdletBinding()]
param(
    [string]$SourcePackage,
    [ValidateSet('CPU', 'CUDA')][string]$Lane = 'CPU',
    [switch]$Promote
)

$ErrorActionPreference = 'Stop'

function Assert-SdrPlainTree([string]$Path) {
    $cursor = Get-Item -LiteralPath $Path -Force
    while ($null -ne $cursor) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point is not admitted: $($cursor.FullName)"
        }
        $cursor = $cursor.Parent
    }
    foreach ($entry in Get-ChildItem -LiteralPath $Path -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point is not admitted: $($entry.FullName)"
        }
    }
}

function Assert-SdrPackageManifest([string]$Package, [string]$Lane) {
    Assert-SdrPlainTree $Package
    $manifestPath = Join-Path $Package 'release_manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 'sdr-native-release-manifest' -or $manifest.schema_version -ne 1 -or
        $manifest.product -ne 'SDR Native Monitoring' -or $manifest.lane -ne $Lane -or
        $manifest.python_abi -ne 'cp313' -or -not $manifest.version -or -not $manifest.files) {
        throw 'Unsupported or incomplete release manifest'
    }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.files) {
        $relative = [string]$entry.path
        if (-not $relative -or [IO.Path]::IsPathRooted($relative) -or $relative.Contains('\') -or
            $relative.Contains(':') -or @($relative.Split('/') | Where-Object {
                $_ -in @('', '.', '..') -or $_ -match '[. ]$|[<>:"|?*\x00-\x1f]' -or
                $_ -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)'
            }).Count) {
            throw "Unsafe manifest path: $relative"
        }
        if (-not $seen.Add($relative)) { throw "Duplicate manifest path: $relative" }
        $file = Get-Item -LiteralPath (Join-Path $Package $relative) -Force
        $expectedPath = [IO.Path]::GetFullPath((Join-Path $Package $relative))
        if ($file.FullName -ne $expectedPath -or $file.PSIsContainer -or $file.Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Manifest mismatch: $relative"
        }
    }
    $actual = @(Get-ChildItem -LiteralPath $Package -Recurse -File -Force | Where-Object {
        $_.FullName -ne $manifestPath
    })
    [string[]]$actualPaths = @($actual | ForEach-Object {
        [IO.Path]::GetRelativePath($Package, $_.FullName).Replace('\', '/')
    })
    if (-not $seen.SetEquals($actualPaths) -or -not $seen.Contains('SDRNativeMonitoring.exe')) {
        throw 'Missing executable or unmanifested package files'
    }
    return $manifest
}

function Assert-SdrCurrentStopped([string]$Current) {
    $prefix = $Current.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    foreach ($process in Get-Process -Name SDRNativeMonitoring -ErrorAction SilentlyContinue) {
        $path = $process.Path
        if (-not $path) { throw 'Cannot establish path of a running SDRNativeMonitoring process' }
        if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Close the current application normally before publishing (PID $($process.Id))"
        }
    }
}

function Publish-SdrCurrent {
    [CmdletBinding()]
    param([string]$RepositoryRoot, [string]$SourcePackage, [string]$Lane, [switch]$Promote)
    if ($Lane -notin @('CPU', 'CUDA')) { throw 'Unsupported lane' }
    if (-not $SourcePackage) { throw 'SourcePackage is required' }
    $dist = (Resolve-Path -LiteralPath (Join-Path $RepositoryRoot 'dist')).Path.TrimEnd('\', '/')
    $source = (Resolve-Path -LiteralPath $SourcePackage).Path.TrimEnd('\', '/')
    $sourceInfo = Get-Item -LiteralPath $source
    # Only a tagged sibling package in this repository can become current.
    if (-not $sourceInfo.PSIsContainer -or $sourceInfo.Name -ne 'SDRNativeMonitoring' -or
        $sourceInfo.Parent.Parent.FullName -ne $dist -or
        $sourceInfo.Parent.Name -notmatch "^SDRNativeMonitoring-$Lane-[A-Za-z0-9][A-Za-z0-9_-]*$") {
        throw 'Source must be a tagged package directly under this repository dist'
    }
    $currentParent = Join-Path $dist "SDRNativeMonitoring-$Lane"
    $current = Join-Path $currentParent 'SDRNativeMonitoring'
    Assert-SdrPlainTree $source
    if (Test-Path -LiteralPath $currentParent) { Assert-SdrPlainTree $currentParent }
    $sourceManifestHash = (Get-FileHash -LiteralPath (Join-Path $source 'release_manifest.json') -Algorithm SHA256).Hash
    $manifest = Assert-SdrPackageManifest $source $Lane
    Assert-SdrCurrentStopped $current
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $archiveRoot = Join-Path $dist 'archive'
    if (Test-Path -LiteralPath $archiveRoot) { Assert-SdrPlainTree $archiveRoot }
    $archive = Join-Path $archiveRoot "SDRNativeMonitoring-$Lane-$stamp"
    $stage = Join-Path $dist ".sdr-current-stage-$Lane-$stamp"
    $result = [ordered]@{ source = $source; current = $current; archive = $archive;
        version = $manifest.version; lane = $Lane; promoted = $false }
    if (-not $Promote) { return [pscustomobject]$result }

    # The complete directory is copied into a fresh sibling, then verified.
    # No merge, recursive deletion, running-process termination, or firewall edit.
    $lock = [IO.File]::Open((Join-Path $dist ".sdr-current-$Lane.lock"),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        if ((Test-Path -LiteralPath $stage) -or (Test-Path -LiteralPath $archive)) {
            throw 'Unique staging/archive path already exists'
        }
        Copy-Item -LiteralPath $source -Destination $stage -Recurse
        if ((Get-FileHash -LiteralPath (Join-Path $stage 'release_manifest.json') -Algorithm SHA256).Hash -ne $sourceManifestHash) {
            throw 'Source manifest changed during promotion'
        }
        $null = Assert-SdrPackageManifest $stage $Lane
        Assert-SdrCurrentStopped $current
        $null = New-Item -ItemType Directory -Path $currentParent -Force
        Assert-SdrPlainTree $currentParent
        $backedUp = $false
        if (Test-Path -LiteralPath $current) {
            $null = New-Item -ItemType Directory -Path $archiveRoot -Force
            Assert-SdrPlainTree $archiveRoot
            # All paths were resolved/validated under the explicit dist root above.
            Move-Item -LiteralPath $current -Destination $archive
            $backedUp = $true
        }
        $installed = $false
        try {
            Move-Item -LiteralPath $stage -Destination $current
            $installed = $true
            $null = Assert-SdrPackageManifest $current $Lane
        } catch {
            # Retain rejected bytes for inspection; restore the previous directory.
            if ($installed) { Move-Item -LiteralPath $current -Destination $stage }
            if ($backedUp -and -not (Test-Path -LiteralPath $current)) {
                Move-Item -LiteralPath $archive -Destination $current
            }
            throw
        }
        $result.promoted = $true
        if (-not $backedUp) { $result.archive = $null }
        return [pscustomobject]$result
    } finally {
        $lock.Dispose()
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Publish-SdrCurrent -RepositoryRoot $PSScriptRoot -SourcePackage $SourcePackage -Lane $Lane -Promote:$Promote |
        ConvertTo-Json
}
