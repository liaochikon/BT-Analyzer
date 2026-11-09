param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [int]$PartSizeMB = 200
)

$ErrorActionPreference = 'Stop'
if ($PartSizeMB -lt 1) {
    throw "PartSizeMB 必須是正整數，目前是 $PartSizeMB。"
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$versionPath = Join-Path $RepoRoot 'VERSION'
$version = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION 必須是主版.次版.修訂，目前是「$version」。"
}

$weights = Join-Path $RepoRoot 'models\bs_roformer_sw.onnx'
if (-not (Test-Path -LiteralPath $weights)) {
    throw '打包缺少 models\bs_roformer_sw.onnx。'
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
        throw "打包缺少 $relative。Release 需要編譯完成，而且專案根目錄要有 models 和 fonts。"
    }
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw '找不到 vswhere，無法附上 Visual C++ 執行階段。'
}
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) {
    throw '找不到 Visual Studio 的 VC++ 工具。'
}
$crt = Get-ChildItem -LiteralPath (Join-Path $install 'VC\Redist\MSVC') -Directory |
    Sort-Object Name -Descending |
    ForEach-Object {
        $dir = Join-Path $_.FullName 'x64\Microsoft.VC143.CRT'
        if (Test-Path -LiteralPath (Join-Path $dir 'vcruntime140.dll')) { $dir }
    } |
    Select-Object -First 1
if (-not $crt) {
    throw '找不到 Microsoft.VC143.CRT。'
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
        throw "打包缺少授權檔：$path"
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
    throw '找不到 7z.exe。打包分割檔需要安裝 7-Zip。'
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
$cudaEp = Join-Path $RepoRoot 'cpp\build\cuda-ep'
$cudaProvider = Join-Path $cudaEp 'onnxruntime_providers_cuda.dll'
$packedCuda = Test-Path -LiteralPath $cudaProvider
if ($packedCuda) {
    # This onnxruntime.dll matches the CUDA provider. CPU graphs still run on it.
    Get-ChildItem -LiteralPath $cudaEp -Filter '*.dll' | Copy-Item -Destination $stage
} else {
    Copy-Item -LiteralPath (Join-Path $ort 'lib\onnxruntime.dll') -Destination $stage
}
Copy-Item -LiteralPath (Join-Path $built 'fonts\sarasa-gothic-tc-nerd-merged.ttf') -Destination (Join-Path $stage 'fonts')
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
    throw 'saves 或 cache 不該進壓縮檔。'
}

$archive = Join-Path $dist "$name.7z"
Push-Location -LiteralPath $dist
try {
    # The folder name is stored as the archive root, so extract yields one directory.
    & $sevenZip a "-t7z" "-mx=1" "-v${PartSizeMB}m" "-y" "--" $archive $name
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip 打包失敗，退出碼 $LASTEXITCODE。"
    }
} finally {
    Pop-Location
}
Remove-Item -LiteralPath $stage -Recurse -Force

$parts = @(Get-ChildItem -LiteralPath $dist -File |
    Where-Object { $_.Name -like "$name.7z.*" } |
    Sort-Object Name)
if ($parts.Count -lt 1) {
    throw '分割失敗，沒有產生分割檔。'
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
    throw ('分割檔以外還留著：' + (($extras | ForEach-Object { $_.Name }) -join ', '))
}
$limit = ([int64]$PartSizeMB * 1MB) + 1MB
for ($i = 0; $i -lt ($parts.Count - 1); $i++) {
    if ($parts[$i].Length -gt $limit) {
        throw "分割檔 $($parts[$i].Name) 大於 $PartSizeMB MB。"
    }
}

Write-Host ''
if ($packedCuda) {
    Write-Host '已附上 CUDA 執行階段（cpp\build\cuda-ep）。沒有 NVIDIA 顯示卡時，設定裡改用 CPU。'
} else {
    Write-Host '沒有 cpp\build\cuda-ep，這份 release 只用 CPU 分軌。'
}
Write-Host "可上傳的分割檔只有這些。用 7-Zip 開啟第一份即可解壓，每份 $PartSizeMB MB："
foreach ($part in $parts) { Write-Host $part.FullName }
