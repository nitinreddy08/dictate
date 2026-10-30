# Dictate installer for Windows 10/11. No admin rights needed, and no Python required: uv brings its own.
#
#   irm https://raw.githubusercontent.com/nitinreddy08/dictate/main/install.ps1 | iex
#
# Installs the `dictate` command for your user, then runs the guided setup (model, mic test, shortcut).
param(
    # A zip of the latest code: works without git, which most Windows PCs don't have
    [string]$Source = "https://github.com/nitinreddy08/dictate/archive/refs/heads/main.zip",
    [switch]$NoSetup
)
if ($env:DICTATE_NO_SETUP) { $NoSetup = $true }   # for `irm … | iex`, which can't pass -NoSetup
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # PowerShell's own progress bar slows downloads a lot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$esc = [char]27
function Rgb($r, $g, $b, $text) { "$esc[1;38;2;${r};${g};${b}m$text$esc[0m" }
function Dim($text) { "$esc[2m$text$esc[0m" }
function Step($n, $title) { Write-Host ""; Write-Host "$esc[1;30;48;2;90;200;250m $n/3 $esc[0m  $esc[1m$title$esc[0m" }
function Ok($text) { Write-Host "  $(Rgb 48 209 88 '✔') $text" }
function Fail($text) { Write-Host "  $(Rgb 255 69 58 '✘') $text"; exit 1 }

Write-Host ""
Write-Host ("  " + (Rgb 255 107 107 "▃") + (Rgb 255 184 77 "▇") + (Rgb 76 217 100 "▅") + (Rgb 191 122 240 "▂") +
    (Rgb 255 214 10 "✦") + "  " + (Rgb 255 107 107 "D") + (Rgb 255 184 77 "ic") + (Rgb 76 217 100 "ta") +
    (Rgb 90 200 250 "t") + (Rgb 191 122 240 "e") + "  " + (Dim "installer"))
Write-Host ("  " + (Dim "Speak anywhere, type nowhere · private and offline"))
Write-Host ("  " + (Dim "Made by ") + (Rgb 255 214 10 "Nitin Reddy") + (Dim " · github.com/nitinreddy08"))

Step 1 "Checking Windows"
$os = [Environment]::OSVersion.Version
if ($os.Major -lt 10) { Fail "Dictate needs Windows 10 or 11." }
if (-not [Environment]::Is64BitOperatingSystem) { Fail "Dictate needs 64-bit Windows." }
Ok "Windows $($os.Major) · 64-bit"

Step 2 "Installing Dictate"
$bin = Join-Path $env:USERPROFILE ".local\bin"
$env:Path = "$bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host ("  " + (Dim "Fetching uv, the tool that installs Dictate and its own copy of Python…"))
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
    $env:Path = "$bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "Couldn't install uv. Check your internet connection." }
}
Ok "uv $((uv --version) -replace '^uv ', '')"
Write-Host ("  " + (Dim "Installing Dictate with its own Python 3.12 (about 250 MB, once)…"))
uv tool install --python 3.12 --force --quiet $Source
if ($LASTEXITCODE -ne 0) { Fail "Installation failed (see the message above)." }
uv tool update-shell --quiet 2>$null | Out-Null
Ok "Installed the dictate command"

if ($NoSetup) { exit 0 }
Step 3 "Setting up"
& dictate setup
