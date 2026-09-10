param([string]$GameData = 'D:/SteamLibrary/steamapps/common/MapleStory/Data')
$ErrorActionPreference = 'Stop'
# 원본 WZ는 읽기만 하고, 레벨·사용 개수별 연마 프레임을 추출합니다.
$target = Join-Path $PSScriptRoot '../assets/polisher/effects'
New-Item -ItemType Directory -Force -Path $target | Out-Null
[void][Reflection.Assembly]::LoadFrom((Join-Path $PSScriptRoot 'wz_cash_exporter/WzComparerR2.WzLib.dll'))
$structure = New-Object WzComparerR2.WzLib.Wz_Structure
$canvasStructure = New-Object WzComparerR2.WzLib.Wz_Structure
$root = $null
$canvasRoot = $null
$records = @()
try {
    $structure.LoadWzFolder((Join-Path $GameData 'UI'), [ref]$root)
    $canvasStructure.LoadWzFolder((Join-Path $GameData 'UI/_Canvas'), [ref]$canvasRoot)
    $img = $root.Nodes['Enchant.img'].Value
    $canvasImg = $canvasRoot.Nodes['Enchant.img'].Value
    if (-not $img.TryExtract() -or -not $canvasImg.TryExtract()) { throw '이미지 읽기 실패' }
    foreach ($level in 4,5) {
        $base = $img.Node.Nodes['effect'].Nodes['accessoryAugment'].Nodes[($level-4).ToString()]
        foreach ($phase in 'try','success','fail') {
            $counts = if ($phase -eq 'try') { 1..5 } else { @(0) }
            foreach ($count in $counts) {
                $item = if ($phase -eq 'try') { $base.Nodes[$phase].Nodes[($count-1).ToString()].Nodes['itemIcon'] } else { $base.Nodes[$phase].Nodes['itemIcon'] }
                foreach ($layer in 'back','front') {
                    foreach ($frame in $item.Nodes[$layer].Nodes) {
                        if ($frame.Text -notmatch '^\d+$') { continue }
                        $png = $frame.Value
                        $link = $frame.Nodes['_outlink'].Value
                        if ($link) {
                            $node = $canvasImg.Node
                            foreach ($part in ($link -split '/Enchant.img/')[1].Split('/')) { $node = $node.Nodes[$part] }
                            $png = $node.Value
                        }
                        $name = "$level-$phase-$count-$layer-$($frame.Text).png"
                        $destination = Join-Path $target $name
                        if (-not (Test-Path -LiteralPath $destination)) {
                            $bitmap = $png.ExtractPng()
                            try { $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $bitmap.Dispose() }
                        }
                        $origin = $frame.Nodes['origin'].Value
                        $records += [pscustomobject]@{ level=$level; count=$count; phase=$phase; layer=$layer; frame=[int]$frame.Text; file=$name; x=[int]$origin.X; y=[int]$origin.Y; delay=[int]$frame.Nodes['delay'].Value }
                    }
                }
            }
        }
    }
    $manifest = Join-Path $target 'frames.json'
    if (Test-Path -LiteralPath $manifest) { throw "기존 프레임 목록 보존: $manifest" }
    $records | ConvertTo-Json | Set-Content -Encoding utf8 $manifest
    Write-Output "추출 완료: $($records.Count) 프레임"
} finally { $structure.Clear(); $canvasStructure.Clear() }
