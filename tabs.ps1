# Desk Board browser-tab helper (runs hidden, only while the memory panel is open).
#   list:  prints the open tabs of Chrome, Edge, Brave, Vivaldi, Opera and Firefox windows as JSON
#   close: closes the tab named -name64 (base64 UTF-8 text) in the browser window -hwnd, like clicking the tab's X
# Uses Windows UI Automation, the same thing screen readers use. Nothing is sent anywhere.
param([string]$mode = "list", [long]$hwnd = 0, [string]$name64 = "")
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$name = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($name64))
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [Windows.Automation.AutomationElement]
$tabCond = New-Object Windows.Automation.PropertyCondition ($AE::ControlTypeProperty), ([Windows.Automation.ControlType]::TabItem)
$btnCond = New-Object Windows.Automation.PropertyCondition ($AE::ControlTypeProperty), ([Windows.Automation.ControlType]::Button)

if ($mode -eq "close") {
  $w = $AE::FromHandle([IntPtr]$hwnd)
  foreach ($t in $w.FindAll([Windows.Automation.TreeScope]::Descendants, $tabCond)) {
    if ($t.Current.Name -ne $name) { continue }
    $b = $t.FindFirst([Windows.Automation.TreeScope]::Descendants, $btnCond)
    if ($b) {
      $b.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
      'OK'; exit
    }
  }
  'NONE'; exit
}

$out = @()
$classes = @("Chrome_WidgetWin_1", "MozillaWindowClass")
foreach ($w in $AE::RootElement.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)) {
  if ($classes -notcontains $w.Current.ClassName) { continue }
  $tabs = $w.FindAll([Windows.Automation.TreeScope]::Descendants, $tabCond)
  foreach ($t in $tabs) {
    if (-not $t.Current.Name) { continue }
    $out += [pscustomobject]@{ pid = $w.Current.ProcessId; hwnd = $w.Current.NativeWindowHandle; name = $t.Current.Name }
  }
}
ConvertTo-Json -InputObject @($out) -Compress
