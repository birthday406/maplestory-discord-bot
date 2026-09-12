[CmdletBinding()]
param(
    [string]$GameData = "D:\SteamLibrary\steamapps\common\MapleStory\Data",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$assetDirectory = Join-Path $ProjectRoot "assets"
$dataDirectory = Join-Path $ProjectRoot "data"
$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("frieren-simulator-" + [Guid]::NewGuid())
$wzLibrary = Join-Path $PSScriptRoot "wz_cash_exporter\WzComparerR2.WzLib.dll"
$mcvDecoder = Join-Path $PSScriptRoot "extract_mcv_last_frame.py"

foreach ($path in $GameData, $wzLibrary, $mcvDecoder) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "필요한 경로를 찾을 수 없습니다: $path"
    }
}
New-Item -ItemType Directory -Force -Path $assetDirectory, $dataDirectory, $temporaryDirectory | Out-Null
[void][Reflection.Assembly]::LoadFrom($wzLibrary)

function Open-WzFolder([string]$relativePath) {
    $structure = New-Object WzComparerR2.WzLib.Wz_Structure
    $root = $null
    $structure.LoadWzFolder((Join-Path $GameData $relativePath), [ref]$root)
    return [PSCustomObject]@{ Structure = $structure; Root = $root }
}

function Get-LinkedCanvas($canvasRoot, $sourceNode) {
    if (-not $sourceNode) {
        return $null
    }
    $link = [string]$sourceNode.Nodes["_outlink"]?.Value
    if (-not $link) {
        return $null
    }
    $parts = $link.Split("/")
    $imageIndex = -1
    for ($index = 0; $index -lt $parts.Count; $index++) {
        if ($parts[$index].EndsWith(".img", [StringComparison]::OrdinalIgnoreCase)) {
            $imageIndex = $index
            break
        }
    }
    if ($imageIndex -lt 0) {
        return $null
    }
    $image = $canvasRoot.Nodes[$parts[$imageIndex]]?.Value
    if (-not $image -or -not $image.TryExtract()) {
        return $null
    }
    $node = $image.Node
    for ($index = $imageIndex + 1; $index -lt $parts.Count; $index++) {
        $node = $node.Nodes[$parts[$index]]
        if (-not $node) {
            $image.Unextract()
            return $null
        }
    }
    return [PSCustomObject]@{ Image = $image; Png = $node.Value }
}

function Save-LinkedCanvas($canvasRoot, $sourceNode, [string]$destination) {
    $linked = Get-LinkedCanvas $canvasRoot $sourceNode
    if (-not $linked -or -not $linked.Png) {
        throw "클라이언트 이미지를 찾지 못했습니다: $destination"
    }
    try {
        $bitmap = $linked.Png.ExtractPng()
        try { $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png) }
        finally { $bitmap.Dispose() }
    }
    finally {
        $linked.Image.Unextract()
    }
}

# 새 원더베리의 실제 달빛 숲 배경과 일반·특별 결과 슬롯을 추출합니다.
$ui = Open-WzFolder "UI"
$uiCanvas = Open-WzFolder "UI\_Canvas"
try {
    $gachapon = $ui.Root.Nodes["UIGachapon.img"].Value
    if (-not $gachapon.TryExtract()) {
        throw "UIGachapon.img를 읽지 못했습니다."
    }
    try {
        $wonderberry = $gachapon.Node.Nodes["wonderBerry"]
        Save-LinkedCanvas $uiCanvas.Root $wonderberry.Nodes["canvas:common"] (Join-Path $assetDirectory "wonderberry-slot-common.png")
        Save-LinkedCanvas $uiCanvas.Root $wonderberry.Nodes["canvas:special"] (Join-Path $assetDirectory "wonderberry-slot-special.png")

        $video = $wonderberry.Nodes["openvideo"].Nodes["intro"].Value
        $videoBytes = New-Object byte[] $video.Length
        $video.CopyTo($videoBytes, 0)
        $videoPath = Join-Path $temporaryDirectory "wonderberry-intro.mcv"
        [IO.File]::WriteAllBytes($videoPath, $videoBytes)
        & python $mcvDecoder $videoPath (Join-Path $assetDirectory "wonderberry-background.png")
        if ($LASTEXITCODE -ne 0) {
            throw "원더베리 MCV0 배경 해석에 실패했습니다. Python OpenCV가 필요합니다."
        }
    }
    finally {
        $gachapon.Unextract()
    }
}
finally {
    $uiCanvas.Structure.Clear()
    $ui.Structure.Clear()
}

# 현재 시그니처 쿠폰과 모든 펫 아이콘을 이름표와 ZIP 하나로 만듭니다.
$stringWz = Open-WzFolder "String"
$petWz = Open-WzFolder "Item\Pet"
$petCanvas = Open-WzFolder "Item\Pet\_Canvas"
$cashWz = Open-WzFolder "Item\Cash"
$cashCanvas = Open-WzFolder "Item\Cash\_Canvas"
$tablePath = Join-Path $dataDirectory "cash-simulator-items.tsv"
$archivePath = Join-Path $dataDirectory "cash-simulator-icons.zip"
$table = New-Object IO.StreamWriter($tablePath, $false, (New-Object Text.UTF8Encoding($false)))
$archiveStream = New-Object IO.FileStream(
    $archivePath,
    [IO.FileMode]::Create,
    [IO.FileAccess]::ReadWrite,
    [IO.FileShare]::None
)
$archive = New-Object IO.Compression.ZipArchive(
    $archiveStream,
    [IO.Compression.ZipArchiveMode]::Create,
    $false
)
try {
    $table.WriteLine("kind`tid`tname`ticon")

    $cashNames = $stringWz.Root.Nodes["Cash.img"].Value
    $cashImage = $cashWz.Root.Nodes["0568.img"].Value
    if (-not $cashNames.TryExtract() -or -not $cashImage.TryExtract()) {
        throw "시그니처 이름 또는 아이콘을 읽지 못했습니다."
    }
    try {
        foreach ($id in 5681543..5681552) {
            $name = [string]$cashNames.Node.Nodes[[string]$id].Nodes["name"].Value
            $key = ([string]$id).PadLeft(8, "0")
            $linked = Get-LinkedCanvas $cashCanvas.Root $cashImage.Node.Nodes[$key].Nodes["info"].Nodes["icon"]
            if (-not $linked -or -not $linked.Png) { continue }
            try {
                $entryName = "signature-$id.png"
                $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
                $stream = $entry.Open()
                $bitmap = $linked.Png.ExtractPng()
                try { $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png) }
                finally { $bitmap.Dispose(); $stream.Dispose() }
                $table.WriteLine("signature`t$id`t$name`t$entryName")
            }
            finally { $linked.Image.Unextract() }
        }

        # 원더베리 표에 함께 나오는 생명의 물도 같은 캐시 아이콘 묶음에서 가져옵니다.
        $waterId = 5689005
        $waterName = [string]$cashNames.Node.Nodes[[string]$waterId].Nodes["name"].Value
        $waterKey = ([string]$waterId).PadLeft(8, "0")
        $water = Get-LinkedCanvas $cashCanvas.Root $cashImage.Node.Nodes[$waterKey].Nodes["info"].Nodes["icon"]
        if ($water -and $water.Png) {
            try {
                $entryName = "wonderberry-$waterId.png"
                $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
                $stream = $entry.Open()
                $bitmap = $water.Png.ExtractPng()
                try { $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png) }
                finally { $bitmap.Dispose(); $stream.Dispose() }
                $table.WriteLine("wonderberry`t$waterId`t$waterName`t$entryName")
            }
            finally { $water.Image.Unextract() }
        }
    }
    finally {
        $cashImage.Unextract()
        $cashNames.Unextract()
    }

    $petNames = $stringWz.Root.Nodes["Pet.img"].Value
    if (-not $petNames.TryExtract()) {
        throw "펫 이름을 읽지 못했습니다."
    }
    try {
        foreach ($nameNode in $petNames.Node.Nodes) {
            $id = [string]$nameNode.Text
            $name = [string]$nameNode.Nodes["name"]?.Value
            $petImage = $petWz.Root.Nodes[($id + ".img")]?.Value
            if (-not $name -or -not $petImage -or -not $petImage.TryExtract()) { continue }
            try {
                $linked = Get-LinkedCanvas $petCanvas.Root $petImage.Node.Nodes["info"].Nodes["icon"]
                if (-not $linked -or -not $linked.Png) { continue }
                try {
                    $entryName = "pet-$id.png"
                    $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
                    $stream = $entry.Open()
                    $bitmap = $linked.Png.ExtractPng()
                    try { $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png) }
                    finally { $bitmap.Dispose(); $stream.Dispose() }
                    $table.WriteLine("pet`t$id`t$name`t$entryName")
                }
                finally { $linked.Image.Unextract() }
            }
            finally { $petImage.Unextract() }
        }
    }
    finally { $petNames.Unextract() }
}
finally {
    $archive.Dispose()
    $archiveStream.Dispose()
    $table.Dispose()
    $cashCanvas.Structure.Clear()
    $cashWz.Structure.Clear()
    $petCanvas.Structure.Clear()
    $petWz.Structure.Clear()
    $stringWz.Structure.Clear()
}

Write-Output "완료: $assetDirectory"
Write-Output "완료: $tablePath"
Write-Output "완료: $archivePath"
