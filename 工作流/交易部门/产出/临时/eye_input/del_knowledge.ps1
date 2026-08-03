Add-Type -AssemblyName Microsoft.VisualBasic
$base = "C:\Users\32032\Desktop\deepseek\量化交易系统"
$files = Get-ChildItem "$base\scripts\output\*"
$count = 0
foreach ($f in $files) {
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($f.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin')
    $count++
}
$transcripts = @("$base\teacher_data.json", "$base\teacher_data_v2.json", "$base\teacher_speech_only.txt")
$tcount = 0
foreach ($p in $transcripts) {
    if (Test-Path $p) {
        [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
        $tcount++
    }
}
Write-Output "DONE: $count docs + $tcount transcripts moved to recycle bin"
