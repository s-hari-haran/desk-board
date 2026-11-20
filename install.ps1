# Desk Board installer: adds "Desk Board" to the Start menu, starts it when you sign in, and opens it now.
# Nothing is downloaded and nothing is installed outside your user account.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# find pythonw.exe (Python without a console window)
$pythonw = $null
foreach ($cmd in @("py", "python")) {
  try {
    $exe = & $cmd -3 -c "import sys, tkinter; print(sys.executable)" 2>$null
    if (-not $exe) { $exe = & $cmd -c "import sys, tkinter; print(sys.executable)" 2>$null }
    if ($exe) {
      $cand = Join-Path (Split-Path $exe) "pythonw.exe"
      if (Test-Path $cand) { $pythonw = $cand; break }
    }
  } catch {}
}
if (-not $pythonw) {
  Write-Host ""
  Write-Host "Python was not found." -ForegroundColor Red
  Write-Host "Install Python 3 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
  exit 1
}

$shell = New-Object -ComObject WScript.Shell
$places = @(
  [Environment]::GetFolderPath("Programs"),   # Start menu
  [Environment]::GetFolderPath("Startup")     # start at sign-in
)
foreach ($dir in $places) {
  $lnk = $shell.CreateShortcut((Join-Path $dir "Desk Board.lnk"))
  $lnk.TargetPath = $pythonw
  $lnk.Arguments = "`"$(Join-Path $here 'board.pyw')`""
  $lnk.WorkingDirectory = $here
  $lnk.IconLocation = (Join-Path $here "deskboard.ico")
  $lnk.Description = "Desk Board - your tasks on the desktop"
  $lnk.Save()
}

Start-Process $pythonw -ArgumentList "`"$(Join-Path $here 'board.pyw')`"" -WorkingDirectory $here
Write-Host ""
Write-Host "Desk Board is installed and running." -ForegroundColor Green
Write-Host "  - It is in your Start menu as 'Desk Board' (right-click it there to pin it to the taskbar)."
Write-Host "  - It starts by itself when you sign in."
Write-Host "  - Press Alt + `` (the key above Tab) to bring it over your windows."
