# Desk Board browser-tab helper (runs hidden, only while the memory panel is open).
#   list:  prints the open tabs of Chrome, Edge, Brave, Vivaldi, Opera and Firefox windows as JSON
#   close: closes one tab in the browser window -hwnd, like clicking the tab's X, then checks it is really gone.
#          The tab is found by its title (-name64, base64 UTF-8, without the "Memory usage - 19 MB" part that
#          changes every few seconds) and, if several tabs share that title, by its position -index.
# Uses Windows UI Automation, the same thing screen readers use. Nothing is sent anywhere.
param([string]$mode = "list", [long]$hwnd = 0, [string]$name64 = "", [int]$index = -1)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [Windows.Automation.AutomationElement]
$Scope = [Windows.Automation.TreeScope]
$tabCond = New-Object Windows.Automation.PropertyCondition ($AE::ControlTypeProperty), ([Windows.Automation.ControlType]::TabItem)
$btnCond = New-Object Windows.Automation.PropertyCondition ($AE::ControlTypeProperty), ([Windows.Automation.ControlType]::Button)
$memRe = '\s-\s(?:high memory usage|memory usage)\s-\s[\d.,]+\s?[KMG]B'

$browsers = @("chrome", "msedge", "brave", "vivaldi", "opera", "opera_gx", "firefox", "arc", "chromium", "thorium", "librewolf", "waterfox", "zen", "floorp")
$walker = [Windows.Automation.TreeWalker]::ControlViewWalker
$docType = [Windows.Automation.ControlType]::Document

function Get-Title($n) { ($n -replace $memRe, '').Trim() }
# Only the tab strip at the top of the browser: skip "tabs" that live inside a web page (Gmail, Calendar side panels...)
function Test-InPage($el) {
  $p = $walker.GetParent($el)
  for ($d = 0; $p -and $d -lt 40; $d++) {
    if ($p.Current.ControlType -eq $docType) { return $true }
    $p = $walker.GetParent($p)
  }
  return $false
}
function Get-Tabs($w) { @($w.FindAll($Scope::Descendants, $tabCond) | Where-Object { $_.Current.Name -and -not (Test-InPage $_) }) }
function Test-Browser($w) {
  try { $browsers -contains (Get-Process -Id $w.Current.ProcessId -ErrorAction Stop).ProcessName.ToLower() } catch { $false }
}

if ($mode -eq "close") {
  $want = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($name64))
  $w = $AE::FromHandle([IntPtr]$hwnd)
  $tabs = Get-Tabs $w
  # every tab with this title; pick the one closest to where it was in the list
  $best = $null; $dist = [int]::MaxValue
  for ($i = 0; $i -lt $tabs.Count; $i++) {
    if ((Get-Title $tabs[$i].Current.Name) -ne $want) { continue }
    $d = [Math]::Abs($i - $index)
    if ($d -lt $dist) { $best = $tabs[$i]; $dist = $d }
  }
  if (-not $best) { 'NONE'; exit }
  $before = $tabs.Count

  # the tab's close button. Brave (and Chrome with narrow tabs) hides it on tabs you're not looking at,
  # so look in the raw tree too, where hidden buttons still are.
  function Find-Close($t) {
    $btns = @($t.FindAll($Scope::Descendants, $btnCond))
    $b = $btns | Where-Object { $_.Current.Name -match 'close' } | Select-Object -Last 1
    if ($b) { return $b }
    $raw = [Windows.Automation.TreeWalker]::RawViewWalker
    $c = $raw.GetFirstChild($t)
    $all = @()
    while ($c) { if ($c.Current.ControlType -eq [Windows.Automation.ControlType]::Button) { $all += $c }; $c = $raw.GetNextSibling($c) }
    $b = $all | Where-Object { $_.Current.Name -match 'close' } | Select-Object -Last 1
    if ($b) { return $b }
    if ($btns.Count -gt 1) { return $btns | Select-Object -Last 1 }   # unnamed: the close button comes after mute
    return $null
  }
  function Wait-Gone {
    for ($k = 0; $k -lt 15; $k++) {          # wait up to 1.5 s for the tab to disappear
      Start-Sleep -Milliseconds 100
      try { if ((Get-Tabs $w).Count -lt $before) { return $true } }
      catch { return $true }                  # that was the last tab, so the whole window closed
    }
    return $false
  }
  function Press($b) {
    try { $b.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke(); return $true } catch { return $false }
  }

  # 1) the close button is showing: press it
  $shown = @($best.FindAll($Scope::Descendants, $btnCond)) | Where-Object { $_.Current.Name -match 'close' } | Select-Object -Last 1
  if ($shown -and (Press $shown) -and (Wait-Gone)) { 'OK'; exit }

  # 2) the button is hidden: switch to that tab for a moment, close it, then switch back to the tab you were on
  $was = $tabs | Where-Object { try { $_.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected } catch { $false } } | Select-Object -First 1
  if ($was -and [Windows.Automation.Automation]::Compare($was, $best)) { $was = $null }   # it was already the one in front
  try { $best.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select() } catch { 'NONE'; exit }
  Start-Sleep -Milliseconds 150
  $b = Find-Close $best
  if (-not $b -or -not (Press $b)) { 'NONE'; exit }
  $ok = Wait-Gone
  if ($was) {
    try { $was.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select() } catch {}
  }
  if ($ok) { 'OK' } else { 'STILL' }
  exit
}

$out = @()
$classes = @("Chrome_WidgetWin_1", "MozillaWindowClass")
foreach ($w in $AE::RootElement.FindAll($Scope::Children, [Windows.Automation.Condition]::TrueCondition)) {
  if ($classes -notcontains $w.Current.ClassName) { continue }
  if (-not (Test-Browser $w)) { continue }     # Slack, Teams, VS Code... are built on Chrome too, but aren't browsers
  $i = 0
  foreach ($t in (Get-Tabs $w)) {
    $out += [pscustomobject]@{ pid = $w.Current.ProcessId; hwnd = $w.Current.NativeWindowHandle; name = $t.Current.Name; idx = $i }
    $i++
  }
}
ConvertTo-Json -InputObject @($out) -Compress
