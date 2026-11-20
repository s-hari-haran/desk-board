# Desk Board image helper (runs hidden, only when you paste or copy an image).
#   paste mode: saves the clipboard image (or a copied image file) as <full>, then makes two rounded previews:
#               <prefix>_w.png (wide, for a card with one image) and <prefix>_s.png (square, for several images)
#   thumbs mode: makes the two previews again from an existing image file (-src)
#   copy mode:  puts the image file -src on the clipboard
# Prints OK when it did something, NONE otherwise.
param([string]$full, [string]$prefix, [int]$ww, [int]$wh, [int]$sq, [int]$r, [string]$src = "", [string]$mode = "paste")
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$png = [System.Drawing.Imaging.ImageFormat]::Png

if ($mode -eq "copy") {
  $i = [System.Drawing.Image]::FromFile($src)
  [Windows.Forms.Clipboard]::SetImage($i)
  'OK'; exit
}

$img = $null
if ($src) {
  $img = [System.Drawing.Image]::FromFile($src)
} else {
  if ([Windows.Forms.Clipboard]::ContainsImage()) {
    $img = [Windows.Forms.Clipboard]::GetImage()
  } elseif ([Windows.Forms.Clipboard]::ContainsFileDropList()) {
    foreach ($f in [Windows.Forms.Clipboard]::GetFileDropList()) {
      if ($f -match '\.(png|jpe?g|gif|bmp|tiff?)$') { try { $img = [System.Drawing.Image]::FromFile($f); break } catch {} }
    }
  }
  if ($img) { $img.Save($full, $png) }
}
if (-not $img) { 'NONE'; exit }

function Save-Preview($img, [int]$w, [int]$h, [int]$r, [string]$path) {
  # fill the box (crop the overflow, keeping the top of tall screenshots), then round the corners
  $scale = [Math]::Max($w / $img.Width, $h / $img.Height)
  $sw = $w / $scale; $sh = $h / $scale
  $sx = ($img.Width - $sw) / 2
  $sy = [Math]::Max(0, ($img.Height - $sh) * 0.15)
  $tmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($tmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.DrawImage($img, (New-Object System.Drawing.RectangleF 0, 0, $w, $h), (New-Object System.Drawing.RectangleF $sx, $sy, $sw, $sh), [System.Drawing.GraphicsUnit]::Pixel)
  $g.Dispose()
  $out = New-Object System.Drawing.Bitmap $w, $h, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($out)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.Clear([System.Drawing.Color]::Transparent)
  $d = 2 * $r
  $p = New-Object System.Drawing.Drawing2D.GraphicsPath
  $p.AddArc(0, 0, $d, $d, 180, 90)
  $p.AddArc($w - $d - 1, 0, $d, $d, 270, 90)
  $p.AddArc($w - $d - 1, $h - $d - 1, $d, $d, 0, 90)
  $p.AddArc(0, $h - $d - 1, $d, $d, 90, 90)
  $p.CloseFigure()
  $g.FillPath((New-Object System.Drawing.TextureBrush $tmp), $p)
  $g.Dispose()
  $out.Save($path, $png)
}
Save-Preview $img $ww $wh $r "$($prefix)_w.png"
Save-Preview $img $sq $sq $r "$($prefix)_s.png"
'OK'
