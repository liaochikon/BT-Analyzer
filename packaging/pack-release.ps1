param(
    [Parameter(Mandatory = $true)][string]$RepoRoot
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$versionPath = Join-Path $RepoRoot 'VERSION'
$version = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION 必須是主版.次版.修訂，目前是「$version」。"
}

$built = Join-Path $RepoRoot 'cpp\build\Release'
$required = @(
    'bt-app.exe',
    'onnxruntime.dll',
    'soxr.dll',
    'models\htdemucs_6s.onnx',
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
$zip = Join-Path $dist "$name.zip"
if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'models') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'fonts') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'licenses') | Out-Null

Copy-Item -LiteralPath (Join-Path $built 'bt-app.exe') -Destination $stage
Copy-Item -LiteralPath (Join-Path $built 'onnxruntime.dll') -Destination $stage
Copy-Item -LiteralPath (Join-Path $built 'soxr.dll') -Destination $stage
Copy-Item -LiteralPath (Join-Path $built 'models\htdemucs_6s.onnx') -Destination (Join-Path $stage 'models')
Copy-Item -LiteralPath (Join-Path $built 'models\nmp.onnx') -Destination (Join-Path $stage 'models')
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
foreach ($doc in @('USAGE.txt', 'THIRD_PARTY_LICENSES.txt')) {
    $text = [System.IO.File]::ReadAllText((Join-Path $RepoRoot "packaging\$doc"))
    [System.IO.File]::WriteAllText((Join-Path $stage $doc), $text, $utf8)
}

if ((Test-Path -LiteralPath (Join-Path $stage 'saves')) -or (Test-Path -LiteralPath (Join-Path $stage 'cache'))) {
    throw 'saves 或 cache 不該進壓縮檔。'
}

Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
Write-Host ''
Write-Host '可上傳 GitHub Releases 的壓縮檔：'
Write-Host $zip
