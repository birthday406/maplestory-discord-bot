param([string]$GameData = 'D:/SteamLibrary/steamapps/common/MapleStory/Data')
$ErrorActionPreference = 'Stop'
# 원본 WZ는 읽기만 하고 이 워크트리의 새 에셋 폴더에만 추출합니다.
$output = Join-Path $PSScriptRoot '../assets/polisher'
New-Item -ItemType Directory -Force -Path $output | Out-Null
[void][Reflection.Assembly]::LoadFrom((Join-Path $PSScriptRoot 'wz_cash_exporter/WzComparerR2.WzLib.dll'))
function Export-Canvas($folder, $imageName, $nodePath, $filename) {
    $structure = New-Object WzComparerR2.WzLib.Wz_Structure
    $root = $null
    try {
        $structure.LoadWzFolder((Join-Path $GameData $folder), [ref]$root)
        $entry = $root.Nodes[$imageName].Value
        if (-not $entry.TryExtract()) { throw "읽기 실패: $imageName" }
        $node = $entry.Node
        foreach ($part in $nodePath.Split('/')) { $node = $node.Nodes[$part] }
        $dest = Join-Path $output $filename
        if (Test-Path -LiteralPath $dest) { Write-Output "기존 파일 유지: $dest"; return }
        $bitmap = $node.Value.ExtractPng()
        try { $bitmap.Save($dest, [System.Drawing.Imaging.ImageFormat]::Png) }
        finally { $bitmap.Dispose() }
        Write-Output $dest
    } finally { $structure.Clear() }
}
Export-Canvas 'UI/_Canvas' 'Enchant.img' 'accessoryAugment/backgrnd' 'background.png'
Export-Canvas 'UI/_Canvas' 'Enchant.img' 'accessoryAugment/layer:onEquip' 'equipped.png'
Export-Canvas 'UI/_Canvas' 'Enchant.img' 'accessoryAugment/layer:probBox' 'probability.png'
Export-Canvas 'Character/Ring/_Canvas' '01113098.img' 'info/icon' 'ring.png'
# 각 아이템의 _outlink가 가리키는 실제 공유 아이콘입니다.
Export-Canvas 'Item/Consume/_Canvas' '0253.img' '02539005/info/icon' 'life.png'
Export-Canvas 'Item/Consume/_Canvas' '0253.img' '02539004/info/icon' 'faith.png'
