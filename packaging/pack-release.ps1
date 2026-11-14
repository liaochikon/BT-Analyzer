param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [int]$PartSizeMB = 200
)

$ErrorActionPreference = 'Stop'
if ($PartSizeMB -lt 1) {
    throw "PartSizeMB must be a positive integer, got $PartSizeMB."
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$versionPath = Join-Path $RepoRoot 'VERSION'
$version = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must be major.minor.patch, got '$version'."
}

$weights = Join-Path $RepoRoot 'models\bs_roformer_sw.onnx'
if (-not (Test-Path -LiteralPath $weights)) {
    throw 'Packaging requires models\bs_roformer_sw.onnx.'
}

$built = Join-Path $RepoRoot 'cpp\build\Release'
$required = @(
    'bt-app.exe',
    'onnxruntime.dll',
    'soxr.dll',
    'models\nmp.onnx',
    'fonts\sarasa-gothic-tc-nerd-merged.ttf'
)
foreach ($relative in $required) {
    $path = Join-Path $built $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Packaging is missing $relative. Finish a Release build and keep models and fonts under the repo root."
    }
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'vswhere not found; cannot attach the Visual C++ runtime.'
}
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) {
    throw 'Visual Studio VC++ tools not found.'
}
$crt = Get-ChildItem -LiteralPath (Join-Path $install 'VC\Redist\MSVC') -Directory |
    Sort-Object Name -Descending |
    ForEach-Object {
        $dir = Join-Path $_.FullName 'x64\Microsoft.VC143.CRT'
        if (Test-Path -LiteralPath (Join-Path $dir 'vcruntime140.dll')) { $dir }
    } |
    Select-Object -First 1
if (-not $crt) {
    throw 'Microsoft.VC143.CRT not found.'
}

$ort = Join-Path $RepoRoot 'cpp\build\onnxruntime-win-x64-1.31.0'
$soxr = Join-Path $RepoRoot 'cpp\build\_deps\soxr-src'
$imgui = Join-Path $RepoRoot 'cpp\build\imgui-1.91.9b\LICENSE.txt'
foreach ($path in @(
        (Join-Path $ort 'LICENSE'),
        (Join-Path $ort 'ThirdPartyNotices.txt'),
        (Join-Path $soxr 'LICENCE'),
        (Join-Path $soxr 'COPYING.LGPL'),
        $imgui,
        (Join-Path $RepoRoot 'LICENSE'))) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Packaging is missing license file: $path"
    }
}

$name = "BT-Analyzer-$version-win64"
$dist = Join-Path $RepoRoot 'dist'
$stage = Join-Path $dist $name
$sevenZip = $null
$sevenZipCommand = Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($sevenZipCommand) {
    $sevenZip = $sevenZipCommand.Source
}
foreach ($candidate in @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'))) {
    if (-not $sevenZip -and (Test-Path -LiteralPath $candidate)) {
        $sevenZip = $candidate
    }
}
if (-not $sevenZip) {
    throw '7z.exe not found. Install 7-Zip to build split archives.'
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
if (Test-Path -LiteralPath $dist) {
    Get-ChildItem -LiteralPath $dist -Force |
        Where-Object {
            $_.Name -like "$name.7z.*" -or
            $_.Name -like "$name.zip.*" -or
            $_.Name -eq "$name.7z" -or
            $_.Name -eq "$name.zip" -or
            $_.Name -eq "$name-join.bat"
        } |
        Remove-Item -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'models') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'fonts') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'locales') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'licenses') | Out-Null

Copy-Item -LiteralPath (Join-Path $built 'bt-app.exe') -Destination $stage
Copy-Item -LiteralPath (Join-Path $built 'soxr.dll') -Destination $stage
# models/bs_roformer_sw.onnx is gitignored. Release parts still include it.
Copy-Item -LiteralPath (Join-Path $built 'models\nmp.onnx') -Destination (Join-Path $stage 'models')
Copy-Item -LiteralPath $weights -Destination (Join-Path $stage 'models')
foreach ($optional in @(
        'models\bs_roformer_sw_fp16.onnx',
        'models\htdemucs_6s.onnx')) {
    $source = Join-Path $RepoRoot $optional
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $stage 'models')
    }
}
# Prefer repo-root cuda-ep from fetch-cuda.bat; fall back to legacy build/cuda-ep.
$cudaEpCandidates = @(
    (Join-Path $RepoRoot 'cuda-ep'),
    (Join-Path $RepoRoot 'cpp\build\cuda-ep')
)
$cudaEp = $null
foreach ($candidate in $cudaEpCandidates) {
    if (Test-Path -LiteralPath (Join-Path $candidate 'onnxruntime_providers_cuda.dll')) {
        $cudaEp = $candidate
        break
    }
}
$packedCuda = $null -ne $cudaEp
if ($packedCuda) {
    # GPU package onnxruntime.dll matches the CUDA provider. CPU graphs still run on it.
    Get-ChildItem -LiteralPath $cudaEp -Filter '*.dll' | Copy-Item -Destination $stage
} else {
    Copy-Item -LiteralPath (Join-Path $ort 'lib\onnxruntime.dll') -Destination $stage
}
Copy-Item -LiteralPath (Join-Path $built 'fonts\sarasa-gothic-tc-nerd-merged.ttf') -Destination (Join-Path $stage 'fonts')
foreach ($locale in @('zh-Hant.txt', 'zh-Hans.txt', 'en.txt')) {
    Copy-Item -LiteralPath (Join-Path $built "locales\$locale") -Destination (Join-Path $stage 'locales')
}
Get-ChildItem -LiteralPath $crt -Filter '*.dll' | Copy-Item -Destination $stage

Copy-Item -LiteralPath (Join-Path $RepoRoot 'LICENSE') -Destination (Join-Path $stage 'LICENSE.txt')
Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'packaging\licenses') -File |
    Copy-Item -Destination (Join-Path $stage 'licenses')
Copy-Item -LiteralPath (Join-Path $ort 'LICENSE') -Destination (Join-Path $stage 'licenses\onnxruntime-MIT.txt')
Copy-Item -LiteralPath (Join-Path $ort 'ThirdPartyNotices.txt') -Destination (Join-Path $stage 'licenses\onnxruntime-ThirdPartyNotices.txt')
Copy-Item -LiteralPath (Join-Path $soxr 'LICENCE') -Destination (Join-Path $stage 'licenses\soxr-LICENCE.txt')
Copy-Item -LiteralPath (Join-Path $soxr 'COPYING.LGPL') -Destination (Join-Path $stage 'licenses\soxr-LGPL-2.1.txt')
Copy-Item -LiteralPath $imgui -Destination (Join-Path $stage 'licenses\imgui-MIT.txt')

$utf8 = New-Object System.Text.UTF8Encoding $true
$utf8Read = New-Object System.Text.UTF8Encoding $false
foreach ($doc in @('USAGE.txt', 'THIRD_PARTY_LICENSES.txt')) {
    $text = [System.IO.File]::ReadAllText((Join-Path $RepoRoot "packaging\$doc"), $utf8Read)
    [System.IO.File]::WriteAllText((Join-Path $stage $doc), $text, $utf8)
}

if ((Test-Path -LiteralPath (Join-Path $stage 'saves')) -or (Test-Path -LiteralPath (Join-Path $stage 'cache'))) {
    throw 'saves or cache must not be included in the archive.'
}

$archive = Join-Path $dist "$name.7z"
Push-Location -LiteralPath $dist
try {
    # The folder name is stored as the archive root, so extract yields one directory.
    & $sevenZip a "-t7z" "-mx=1" "-v${PartSizeMB}m" "-y" "--" $archive $name
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip packaging failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
Remove-Item -LiteralPath $stage -Recurse -Force

$parts = @(Get-ChildItem -LiteralPath $dist -File |
    Where-Object { $_.Name -like "$name.7z.*" } |
    Sort-Object Name)
if ($parts.Count -lt 1) {
    throw 'Split failed; no archive parts were produced.'
}
$extras = @(Get-ChildItem -LiteralPath $dist -Force |
    Where-Object {
        $_.Name -eq $name -or
        $_.Name -eq "$name.7z" -or
        $_.Name -eq "$name.zip" -or
        $_.Name -eq "$name-join.bat" -or
        $_.Name -like "$name.zip.*"
    })
if ($extras.Count -gt 0) {
    throw ('Leftover files besides split parts: ' + (($extras | ForEach-Object { $_.Name }) -join ', '))
}
$limit = ([int64]$PartSizeMB * 1MB) + 1MB
for ($i = 0; $i -lt ($parts.Count - 1); $i++) {
    if ($parts[$i].Length -gt $limit) {
        throw "Split part $($parts[$i].Name) is larger than $PartSizeMB MB."
    }
}

Write-Host ''
if ($packedCuda) {
    Write-Host "CUDA runtime included ($cudaEp). Use CPU in settings when no NVIDIA GPU is present."
} else {
    Write-Host 'No cuda-ep\; this release runs stem separation on CPU only. Run fetch-cuda.bat to add it.'
}
Write-Host "Upload these split parts only. Open the first with 7-Zip to extract. Each part is $PartSizeMB MB:"
foreach ($part in $parts) { Write-Host $part.FullName }
