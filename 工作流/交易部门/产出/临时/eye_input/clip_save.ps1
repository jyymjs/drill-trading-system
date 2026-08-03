Add-Type -AssemblyName System.Windows.Forms
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -ne $null) {
    $img.Save("C:\Users\32032\Desktop\deepseek\量化交易系统\temp\eye_input\user_1.png")
    Write-Output "SAVED"
} else {
    Write-Output "NO_IMAGE"
}
