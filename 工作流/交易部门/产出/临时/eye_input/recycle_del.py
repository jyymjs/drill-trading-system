# -*- coding: utf-8 -*-
"""用 EncodedCommand 方式执行 PowerShell 回收站删除（绕开中文编码问题）"""
import base64, subprocess

ps = r"""
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
"""
b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
r = subprocess.run(
    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", b64],
    capture_output=True)
out = r.stdout.decode("gbk", errors="replace").strip()
err = r.stderr.decode("gbk", errors="replace").strip()
print(out)
if err:
    print("STDERR:", err)
