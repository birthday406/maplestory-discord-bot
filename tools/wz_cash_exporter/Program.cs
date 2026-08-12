using System;
using System.Collections.Generic;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Compression;
using System.Linq;
using WzComparerR2.WzLib;

if (args.Length != 5)
{
    Console.Error.WriteLine(
        "사용법: wz-cash-exporter <GMS String> <KMS String> <GMS Character> <TSV> <ZIP>"
    );
    return 2;
}

var gmsNames = LoadEquipNames(args[0]);
var kmsNames = LoadEquipNames(args[1]);
var exported = 0;
var iconCount = 0;

// 출력 폴더는 PowerShell 실행 도구가 미리 만든 임시 폴더입니다.
using var table = new StreamWriter(args[3], false, new System.Text.UTF8Encoding(false));
using var archive = ZipFile.Open(args[4], ZipArchiveMode.Create);
table.WriteLine("id\tcategory\tgms_name\tkms_name\ticon");

foreach (var category in gmsNames.Values.Select(item => item.Category).Distinct().OrderBy(name => name))
{
    var folderName = category == "Taming" ? "TamingMob" : category;
    var folder = Path.Combine(args[2], folderName);
    var canvasFolder = Path.Combine(folder, "_Canvas");
    if (!Directory.Exists(folder) || !Directory.Exists(canvasFolder))
    {
        continue;
    }

    var categoryStructure = new Wz_Structure();
    var canvasStructure = new Wz_Structure();
    Wz_Node categoryRoot = null;
    Wz_Node canvasRoot = null;
    var categoryExported = 0;
    try
    {
        categoryStructure.LoadWzFolder(folder, ref categoryRoot);
        canvasStructure.LoadWzFolder(canvasFolder, ref canvasRoot);
        foreach (var id in gmsNames.Keys
            .Where(id => gmsNames[id].Category == category)
            .OrderBy(id => id))
        {
            var imageName = id.PadLeft(8, '0') + ".img";
            var itemImage = categoryRoot.Nodes[imageName]?.GetValue<Wz_Image>();
            if (itemImage == null || !itemImage.TryExtract()
                || itemImage.Node.Nodes["info"]?.Nodes["cash"]?.GetValue<int>() != 1)
            {
                itemImage?.Unextract();
                continue;
            }

            var hasIcon = false;
            Wz_Image canvasImage = null;
            try
            {
                var icon = FindIcon(itemImage, canvasRoot, imageName);
                canvasImage = icon.Image;
                if (icon.Png != null)
                {
                    var entry = archive.CreateEntry(id + ".png", CompressionLevel.Optimal);
                    using var stream = entry.Open();
                    using var bitmap = icon.Png.ExtractPng();
                    bitmap.Save(stream, ImageFormat.Png);
                    hasIcon = true;
                    iconCount++;
                }
            }
            catch
            {
                // 헤어·성형처럼 독립 아이콘이 없는 항목도 이름 검색에는 남깁니다.
                hasIcon = false;
            }
            finally
            {
                canvasImage?.Unextract();
                itemImage.Unextract();
            }

            var kmsName = kmsNames.TryGetValue(id, out var kmsItem) ? kmsItem.Name : "";
            table.WriteLine(
                $"{id}\t{Clean(category)}\t{Clean(gmsNames[id].Name)}\t{Clean(kmsName)}\t{(hasIcon ? id + ".png" : "")}"
            );
            exported++;
            categoryExported++;
        }
    }
    finally
    {
        canvasStructure.Clear();
        categoryStructure.Clear();
    }
    Console.WriteLine($"{category}: {categoryExported:N0}개");
}

Console.WriteLine($"완료: 아이템 {exported:N0}개, 아이콘 {iconCount:N0}개");
return exported > 0 ? 0 : 1;

static Dictionary<string, (string Category, string Name)> LoadEquipNames(string folder)
{
    var structure = new Wz_Structure();
    Wz_Node root = null;
    try
    {
        structure.LoadWzFolder(folder, ref root);
        var image = root.Nodes["Eqp.img"]?.GetValue<Wz_Image>();
        if (image == null || !image.TryExtract())
        {
            throw new Exception($"Eqp.img 파일을 읽을 수 없습니다: {folder}");
        }

        var result = new Dictionary<string, (string Category, string Name)>();
        foreach (var category in image.Node.Nodes["Eqp"].Nodes)
        {
            foreach (var item in category.Nodes)
            {
                var name = item.Nodes["name"]?.GetValue<string>();
                if (!string.IsNullOrWhiteSpace(name))
                {
                    result[item.Text] = (category.Text, name);
                }
            }
        }
        return result;
    }
    finally
    {
        structure.Clear();
    }
}

static string Clean(string value)
{
    return value.Replace('\t', ' ').Replace('\r', ' ').Replace('\n', ' ');
}

static (Wz_Png Png, Wz_Image Image) FindIcon(
    Wz_Image itemImage,
    Wz_Node canvasRoot,
    string imageName
)
{
    var info = itemImage.Node.Nodes["info"];
    var source = info?.Nodes["icon"] ?? info?.Nodes["iconRaw"];
    var link = source?.Nodes["_outlink"]?.GetValue<string>();
    if (!string.IsNullOrWhiteSpace(link))
    {
        var parts = link.Split('/');
        var imageIndex = Array.FindIndex(
            parts,
            part => part.EndsWith(".img", StringComparison.OrdinalIgnoreCase)
        );
        if (imageIndex >= 0)
        {
            var linkedImage = canvasRoot.Nodes[parts[imageIndex]]?.GetValue<Wz_Image>();
            if (linkedImage != null && linkedImage.TryExtract())
            {
                var node = linkedImage.Node;
                for (var index = imageIndex + 1; index < parts.Length && node != null; index++)
                {
                    node = node.Nodes[parts[index]];
                }
                return (node?.GetValue<Wz_Png>(), linkedImage);
            }
        }
    }

    var canvasImage = canvasRoot.Nodes[imageName]?.GetValue<Wz_Image>();
    if (canvasImage != null && canvasImage.TryExtract())
    {
        var canvasInfo = canvasImage.Node.Nodes["info"];
        return (
            canvasInfo?.Nodes["icon"]?.GetValue<Wz_Png>()
                ?? canvasInfo?.Nodes["iconRaw"]?.GetValue<Wz_Png>(),
            canvasImage
        );
    }
    return (null, null);
}
