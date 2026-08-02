$base = "E:\tool\biancheng\VScode project 3\omnicrawler2.1.0\source_extracted\OmniCrawler-2.1.0-Source\src\omnicrawl\templates"
$yamlFiles = Get-ChildItem -Path $base -Recurse -Include "*.yaml","*.yml"
$added = 0
$skipped = 0
foreach ($f in $yamlFiles) {
    $content = Get-Content $f.FullName -Raw
    if ($content -match "template_version") {
        $skipped++
    } else {
        $newContent = "template_version: 1`n" + $content
        Set-Content -Path $f.FullName -Value $newContent -NoNewline -Encoding UTF8
        $added++
        Write-Host "Added: $($f.FullName.Substring($base.Length))"
    }
}
Write-Host "`nTotal: $($yamlFiles.Count) files, Added: $added, Skipped: $skipped"
