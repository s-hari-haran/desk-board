# Removes the Desk Board shortcuts and closes it. Your boards stay in the "boards" folder; delete this folder to remove everything.
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Startup"))) {
  Remove-Item (Join-Path $dir "Desk Board.lnk") -ErrorAction SilentlyContinue
}
# stop the keeper first (it would start the board again), then the board itself
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like '*board.pyw*' } |
  Sort-Object { $_.CommandLine -like '*--child*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Desk Board is closed and its shortcuts are removed." -ForegroundColor Green
