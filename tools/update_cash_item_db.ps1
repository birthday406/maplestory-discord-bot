[CmdletBinding()]
param(
    [string]$GmsDataPath = "D:\SteamLibrary\steamapps\common\MapleStory\Data",
    [string]$KmsDataPath = "C:\KMS\Maple\Data",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "data"
}

$gmsString = Join-Path $GmsDataPath "String"
$kmsString = Join-Path $KmsDataPath "String"
$gmsCharacter = Join-Path $GmsDataPath "Character"
$exporter = Join-Path $PSScriptRoot "wz_cash_exporter\wz-cash-exporter.dll"

# 경로를 잘못 입력했을 때 기존 데이터가 지워지기 전에 바로 멈춥니다.
foreach ($folder in $gmsString, $kmsString, $gmsCharacter) {
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
        throw "필요한 게임 폴더를 찾을 수 없습니다: $folder"
    }
}
if (-not (Test-Path -LiteralPath $exporter -PathType Leaf)) {
    throw "WZ 추출기를 찾을 수 없습니다: $exporter"
}
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET 실행 프로그램을 찾을 수 없습니다. .NET 6 Desktop Runtime을 설치해주세요."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempDirectory = Join-Path $tempRoot ("maple-cash-db-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $tempDirectory | Out-Null

try {
    $newTable = Join-Path $tempDirectory "cash-items.tsv"
    $newArchive = Join-Path $tempDirectory "cash-item-icons.zip"

    Write-Host "게임 파일에서 캐시 아이템을 읽는 중입니다. 몇 분 걸릴 수 있습니다."
    & dotnet $exporter $gmsString $kmsString $gmsCharacter $newTable $newArchive
    if ($LASTEXITCODE -ne 0) {
        throw "WZ 추출기가 오류 코드 $LASTEXITCODE 로 종료되었습니다."
    }

    # 비정상적으로 작은 결과는 기존 정상 DB를 덮어쓰지 못하게 막습니다.
    $header = Get-Content -LiteralPath $newTable -Encoding utf8 -TotalCount 1
    $itemCount = (Get-Content -LiteralPath $newTable -Encoding utf8).Count - 1
    if ($header -ne "id`tcategory`tgms_name`tkms_name`ticon" -or $itemCount -lt 1000) {
        throw "생성된 아이템 목록이 올바르지 않습니다. 기존 DB는 변경하지 않았습니다."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($newArchive)
    try {
        $iconCount = $zip.Entries.Count
    }
    finally {
        $zip.Dispose()
    }
    if ($iconCount -lt 100) {
        throw "생성된 아이콘 ZIP이 올바르지 않습니다. 기존 DB는 변경하지 않았습니다."
    }

    # 두 파일을 모두 검증한 뒤에만 봇이 사용하는 실제 파일을 교체합니다.
    Copy-Item -LiteralPath $newArchive -Destination (Join-Path $OutputDirectory "cash-item-icons.zip") -Force
    Copy-Item -LiteralPath $newTable -Destination (Join-Path $OutputDirectory "cash-items.tsv") -Force
    Write-Host "완료: 아이템 $itemCount 개, 아이콘 $iconCount 개"
}
finally {
    # 이 실행에서 만든 Windows 임시 폴더만 정확히 확인하고 정리합니다.
    $resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)
    if ($resolvedTemp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
