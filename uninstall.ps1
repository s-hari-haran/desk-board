# Removes the Desk Board shortcuts and closes it. Your boards stay in the "boards" folder; delete this folder to remove everything.
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Startup"))) {
  Remove-Item (Join-Path $dir "Desk Board.lnk") -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like '*board.pyw*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Desk Board is closed and its shortcuts are removed." -ForegroundColor Green
