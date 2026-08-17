#Requires -Version 5.1
<#
  ============================================================================
  CLAUDE + WSL LEAN-SPACE CLEANUP  (v3)
  ----------------------------------------------------------------------------
  Focused ONLY on the two real space culprits:
      1. Claude Desktop / Cowork  (UWP package, VM bundles, caches, sessions)
      2. WSL2 Ubuntu disk         (clean INSIDE -> sparse -> trim -> compact,
                                   or RELOCATE to another drive)

  Does NOT touch Windows system folders (no C:\Windows, DNS, WinSxS, etc).
  Self-elevates and seizes ownership (takeown/icacls) of protected Claude
  folders so deletes don't fail on permissions.

  WHAT'S NEW IN v3 (from a real debugging session):
   * The WSL disk only shrinks if files are first DELETED inside it. v3 runs
     safe cache cleans inside the distro (apt/npm/pip/journal, and conda with
     -DeepWslClean) BEFORE trimming - otherwise compaction reclaims nothing.
   * Correct order: clean -> shutdown -> set-sparse -> fstrim -> shutdown.
     (Older code trimmed before enabling sparse, which did nothing.)
   * Measures ACTUAL on-disk size (sparse-aware), not the logical .Length.
   * `wsl --manage --optimize` only exists on WSL >= 2.5. v3 detects this and,
     if absent, tells you the clean fix is a relocate/re-import.
   * -RelocateWslTo <drive>  exports a distro and re-imports it onto another
     drive (e.g. D:). This frees C: permanently AND rebuilds a compact disk.
     Safe: keeps the export .tar until the new copy is verified.

  RUN FROM A NORMAL POWERSHELL WINDOW (not from inside Claude) - it can close
  Claude Desktop to unlock files.

  MODES:
    -Report                 Dry run. Shows what WOULD happen. No changes.
    -Auto                   Clears only GREEN/SAFE items (no prompts).
    -StopApps               Close Claude + shut WSL without asking.
    -MinFreeGB <n>          Target for the pass/fail summary (default 15).
    -DeepWslClean           Also run 'conda clean --all' inside the distro.
    -RelocateWslTo <drive>  Move WSL distro(s) to that drive, e.g. -RelocateWslTo D

  Examples:
    .\CLEANUP_v3.ps1 -Report
    .\CLEANUP_v3.ps1 -DeepWslClean
    .\CLEANUP_v3.ps1 -RelocateWslTo D
    .\CLEANUP_v3.ps1 -Auto -MinFreeGB 20
  ============================================================================
#>
[CmdletBinding()]
param(
    [switch]$Report,
    [switch]$Auto,
    [switch]$StopApps,
    [double]$MinFreeGB = 15,
    [switch]$DeepWslClean,
    [string]$RelocateWslTo = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

# ============================= SELF-ELEVATION =============================
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "  Re-launching as Administrator..." -ForegroundColor Yellow
    $fwd = @('-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"")
    if ($Report)        { $fwd += '-Report' }
    if ($Auto)          { $fwd += '-Auto' }
    if ($StopApps)      { $fwd += '-StopApps' }
    if ($DeepWslClean)  { $fwd += '-DeepWslClean' }
    if ($RelocateWslTo) { $fwd += @('-RelocateWslTo', $RelocateWslTo) }
    $fwd += @('-MinFreeGB', $MinFreeGB)
    Start-Process -Verb RunAs -FilePath "powershell.exe" -ArgumentList $fwd
    exit
}

# ============================= GLOBALS / PATHS =============================
$LogFile = "$env:USERPROFILE\Desktop\CLEANUP_$(Get-Date -f 'yyyyMMdd_HHmmss').log"
$user    = $env:USERPROFILE
$roaming = $env:APPDATA
$local   = $env:LOCALAPPDATA
$uwpBase = "$local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude"
$vmDir   = "$uwpBase\vm_bundles"

$script:Freed     = 0
$script:WouldFree = 0
$startFree        = [math]::Round((Get-PSDrive C).Free / 1GB, 2)

function Write-Log($msg) { Add-Content -Path $LogFile -Value "[$(Get-Date -f 'HH:mm:ss')] $msg" }
function Get-FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }

function Format-Bytes($bytes) {
    if ($null -eq $bytes -or $bytes -eq 0) { return "0 B" }
    $u = @("B","KB","MB","GB","TB"); $i = 0; $v = [double]$bytes
    while ($v -ge 1024 -and $i -lt 4) { $v = $v / 1024; $i++ }
    return ("{0:N1} {1}" -f $v, $u[$i])
}

function Get-FolderSize($path) {
    if (-not (Test-Path $path)) { return 0 }
    try {
        $sum = (Get-ChildItem -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { -not $_.PSIsContainer } | Measure-Object -Property Length -Sum).Sum
        if ($null -eq $sum) { return 0 } else { return $sum }
    } catch { return 0 }
}
function Get-FileSize($path) {
    if (-not (Test-Path $path)) { return 0 }
    try { return (Get-Item -LiteralPath $path -Force -EA SilentlyContinue).Length } catch { return 0 }
}
function Get-PathSize($path) {
    if (-not (Test-Path $path)) { return 0 }
    if ((Get-Item -LiteralPath $path -Force).PSIsContainer) { return Get-FolderSize $path }
    return Get-FileSize $path
}

# Actual on-disk allocation (sparse-aware) - logical .Length lies for sparse VHDs
Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint GetCompressedFileSize(string p,out uint h);' -Name SizeApi -Namespace Win32 -EA SilentlyContinue
function Get-OnDiskSize($path) {
    if (-not (Test-Path $path)) { return 0 }
    try { $hi = 0; $lo = [Win32.SizeApi]::GetCompressedFileSize($path, [ref]$hi); return ([uint64]$hi * 4GB) + $lo }
    catch { return (Get-Item -LiteralPath $path -Force).Length }
}

function Grant-FullAccess([string]$p) {
    if (-not (Test-Path $p)) { return }
    try {
        if ((Get-Item -LiteralPath $p -Force).PSIsContainer) { & takeown /F "$p" /R /D Y 2>&1 | Out-Null }
        else { & takeown /F "$p" 2>&1 | Out-Null }
        & icacls "$p" /grant "*S-1-5-32-544:(OI)(CI)F" /T /C /Q 2>&1 | Out-Null
        & icacls "$p" /grant "${env:USERNAME}:(OI)(CI)F"  /T /C /Q 2>&1 | Out-Null
    } catch { }
}

function Remove-PathSafely([string]$p) {
    if (-not (Test-Path $p)) { return [pscustomobject]@{ ok=$true; bytes=0; missing=$true } }
    $bytes = Get-PathSize $p
    try {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop
        return [pscustomobject]@{ ok=$true; bytes=$bytes; note='' }
    } catch {
        Grant-FullAccess $p
        try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop; return [pscustomobject]@{ ok=$true; bytes=$bytes; note='after takeown' } }
        catch { return [pscustomobject]@{ ok=$false; bytes=0; err=$_.Exception.Message } }
    }
}

function Invoke-Cleanup {
    param([string]$Label,[string[]]$Paths,[ValidateSet('SAFE','REVIEW')][string]$Tier='SAFE',[string]$Advice='')
    $existing = @($Paths | Where-Object { Test-Path $_ })
    if ($existing.Count -eq 0) { Write-Host "  -> $Label : not present, skipping." -ForegroundColor DarkGray; return }
    $total = 0; foreach ($p in $existing) { $total += Get-PathSize $p }

    if ($Report) {
        $tag = if ($Tier -eq 'SAFE') { 'SAFE  ' } else { 'REVIEW' }
        Write-Host ("  [{0}] would free {1,10}  :  {2}" -f $tag,(Format-Bytes $total),$Label) -ForegroundColor $(if($Tier -eq 'SAFE'){'Green'}else{'Yellow'})
        $script:WouldFree += $total; return
    }
    $proceed = $false
    if ($Auto) {
        if ($Tier -eq 'SAFE') { $proceed = $true; Write-Host "  [AUTO] $Label ($(Format-Bytes $total))" -ForegroundColor Green }
        else { Write-Host "  -> [AUTO] skipping REVIEW: $Label" -ForegroundColor DarkGray; return }
    } else {
        Write-Host ""; Write-Host "  ======================================================" -ForegroundColor DarkCyan
        Write-Host "  ITEM : $Label"; Write-Host "  SIZE : $(Format-Bytes $total)"
        Write-Host "  TIER : $Tier" -ForegroundColor $(if($Tier -eq 'SAFE'){'Green'}else{'Yellow'})
        if ($Advice) { Write-Host "  NOTE : $Advice" -ForegroundColor Cyan }
        Write-Host "  ======================================================" -ForegroundColor DarkCyan
        Write-Host "  DELETE? [y/N]: " -ForegroundColor Yellow -NoNewline
        $proceed = ((Read-Host) -match '^[Yy]$')
    }
    if (-not $proceed) { Write-Host "    Skipped." -ForegroundColor DarkGray; return }
    $freed = 0; $failed = 0
    foreach ($p in $existing) {
        $r = Remove-PathSafely $p
        if ($r.ok -and -not $r.missing) { $freed += $r.bytes; Write-Host "    OK Removed: $p" -ForegroundColor Green; Write-Log "DELETED: $p ($(Format-Bytes $r.bytes))" }
        elseif (-not $r.ok) { $failed++; Write-Host "    FAILED (in use?): $p" -ForegroundColor DarkYellow; Write-Log "FAILED: $p | $($r.err)" }
    }
    if ($failed -gt 0) { Write-Host "    TIP: stop Claude Desktop / WSL so the file unlocks." -ForegroundColor DarkYellow }
    Write-Host "    Freed: $(Format-Bytes $freed)" -ForegroundColor Green
    $script:Freed += $freed
}

function Stop-ClaudeAndWsl {
    Write-Host "  Stopping Claude Desktop + shutting down WSL..." -ForegroundColor Yellow
    Get-Process -Name 'Claude' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2; try { & wsl --shutdown 2>&1 | Out-Null } catch { }; Start-Sleep 3
    Write-Log "Stopped Claude + wsl --shutdown"
}

# ---- Does this WSL build support 'wsl --manage --optimize'? (>= 2.5) ----
function Test-WslOptimizeSupported {
    try { $h = (& wsl --manage 2>&1 | Out-String); return ($h -match 'optimize') } catch { return $false }
}

# ---- Enumerate WSL2 distros and their VHDs from the registry ----
function Get-WslDistros {
    $out = @()
    foreach ($reg in @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -EA SilentlyContinue)) {
        $p = Get-ItemProperty $reg.PSPath -EA SilentlyContinue
        if (-not $p -or -not $p.BasePath) { continue }
        $base = $p.BasePath -replace '^\\\\\?\\',''
        $vhd  = "$base\ext4.vhdx"
        if (Test-Path $vhd) {
            $out += [pscustomobject]@{ Name=$p.DistributionName; Vhd=$vhd; Base=$base; RegPath=$reg.PSPath; Uid=$p.DefaultUid }
        }
    }
    return $out
}

# ============================================================================
#  WSL: clean inside -> sparse -> trim -> (optimize if supported)
# ============================================================================
function Optimize-WslDisk($d) {
    $beforeDisk = Get-OnDiskSize $d.Vhd
    Write-Host ""
    Write-Host "  -- $($d.Name)  (on disk: $(Format-Bytes $beforeDisk)) --" -ForegroundColor White

    # 1) Clean caches INSIDE the distro so there is something to reclaim.
    Write-Host "     1) cleaning caches inside $($d.Name) ..." -ForegroundColor DarkGray
    $inside = 'apt-get clean 2>/dev/null; journalctl --vacuum-size=50M 2>/dev/null | tail -1; rm -rf ~/.npm/_cacache 2>/dev/null; pip cache purge 2>/dev/null | tail -1; echo done'
    if ($DeepWslClean) {
        $inside = 'for c in $(ls $HOME/miniconda3/bin/conda $HOME/anaconda3/bin/conda 2>/dev/null); do "$c" clean --all -y 2>/dev/null | tail -3; done; ' + $inside
        Write-Host "        (deep: conda clean --all - caches only, environments kept)" -ForegroundColor DarkGray
    }
    try { & wsl -d $($d.Name) -- bash -lc $inside 2>&1 | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray } } catch { }

    # 2) Stop, enable sparse, THEN trim (order matters: sparse must be on first)
    Write-Host "     2) shutdown + enable sparse ..." -ForegroundColor DarkGray
    try { & wsl --shutdown 2>&1 | Out-Null } catch { }; Start-Sleep 4
    try { & wsl --manage $($d.Name) --set-sparse true 2>&1 | Out-Null } catch { Write-Host "        (set-sparse unavailable)" -ForegroundColor DarkGray }

    Write-Host "     3) fstrim (reclaims freed blocks on the sparse disk) ..." -ForegroundColor DarkGray
    try { & wsl -d $($d.Name) -u root -- fstrim -v / 2>&1 | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray } } catch { }
    try { & wsl --shutdown 2>&1 | Out-Null } catch { }; Start-Sleep 3

    # 4) In-place compaction only if this WSL build supports it
    if (Test-WslOptimizeSupported) {
        Write-Host "     4) wsl --manage --optimize ..." -ForegroundColor DarkGray
        try { & wsl --manage $($d.Name) --optimize 2>&1 | Out-Null } catch { }
    } else {
        Write-Host "     4) (this WSL build has no --optimize; sparse+trim only)" -ForegroundColor DarkGray
    }

    $afterDisk = Get-OnDiskSize $d.Vhd
    $saved = $beforeDisk - $afterDisk
    if ($saved -gt 50MB) {
        Write-Host "     DONE: on disk $(Format-Bytes $beforeDisk) -> $(Format-Bytes $afterDisk) (freed $(Format-Bytes $saved))" -ForegroundColor Green
        Write-Log "WSL $($d.Name): freed $(Format-Bytes $saved)"; $script:Freed += $saved
    } else {
        Write-Host "     Little reclaimed in place. For a full compaction, relocate/re-import:" -ForegroundColor Yellow
        Write-Host "       .\CLEANUP_v3.ps1 -RelocateWslTo D    (or update WSL: wsl --update)" -ForegroundColor Yellow
    }
}

# ============================================================================
#  WSL RELOCATE: export -> unregister -> import onto another drive
#  (frees the source drive AND rebuilds a compact disk). Keeps tar until OK.
# ============================================================================
function Move-WslToDrive($d, [string]$driveLetter) {
    $drive = $driveLetter.TrimEnd(':','\')
    $destDir = "${drive}:\WSL\$($d.Name)"
    $tar     = "${drive}:\WSL\$($d.Name)_export.tar"
    Write-Host ""
    Write-Host "  == RELOCATE $($d.Name) -> ${drive}: ==" -ForegroundColor Magenta
    if (-not (Test-Path "${drive}:\")) { Write-Host "    Drive ${drive}: not found. Skipping." -ForegroundColor Red; return }
    $needGB = [math]::Round((Get-OnDiskSize $d.Vhd)/1GB,2)
    $freeGB = [math]::Round((Get-PSDrive $drive -EA SilentlyContinue).Free/1GB,2)
    Write-Host "    Needs ~$needGB GB (x2 transiently for the .tar); ${drive}: has $freeGB GB free." -ForegroundColor DarkGray
    if ($Report) { Write-Host "    [REPORT] would export, unregister, and re-import here." -ForegroundColor Green; return }
    if (-not $Auto) {
        Write-Host "    Proceed? This briefly stops WSL. [y/N]: " -ForegroundColor Yellow -NoNewline
        if ((Read-Host) -notmatch '^[Yy]$') { Write-Host "    Skipped." -ForegroundColor DarkGray; return }
    }
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    try {
        Write-Host "    exporting (this can take several minutes)..." -ForegroundColor DarkGray
        & wsl --shutdown 2>&1 | Out-Null; Start-Sleep 4
        & wsl --export $($d.Name) "$tar" | Out-Null
        $tarGB = [math]::Round((Get-Item $tar).Length/1GB,2)
        if (-not (Test-Path $tar) -or $tarGB -lt 0.2) { Write-Host "    Export failed/too small - distro left intact." -ForegroundColor Red; return }
        Write-Host "    exported $tarGB GB; unregistering old copy (frees source drive)..." -ForegroundColor DarkGray
        & wsl --unregister $($d.Name) | Out-Null
        Write-Host "    importing onto ${drive}: ..." -ForegroundColor DarkGray
        & wsl --import $($d.Name) "$destDir" "$tar" | Out-Null
        # restore default user
        if ($d.Uid) {
            & wsl -d $($d.Name) -u root -- sh -c "grep -q '\[user\]' /etc/wsl.conf 2>/dev/null || printf '[user]\ndefault=%s\n' \$(id -un $($d.Uid)) >> /etc/wsl.conf" 2>&1 | Out-Null
            & wsl --terminate $($d.Name) 2>&1 | Out-Null
            foreach ($reg in @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -EA SilentlyContinue)) {
                $p = Get-ItemProperty $reg.PSPath -EA SilentlyContinue
                if ($p.DistributionName -eq $($d.Name)) { Set-ItemProperty $reg.PSPath -Name DefaultUid -Value $d.Uid }
            }
        }
        $ok = (& wsl -d $($d.Name) -- echo ok 2>&1) -match 'ok'
        if ($ok) {
            Write-Host "    OK $($d.Name) now on $destDir and verified." -ForegroundColor Green
            Write-Log "RELOCATED $($d.Name) -> $destDir"
            Write-Host "    Backup kept at: $tar  (delete it once you're happy)." -ForegroundColor DarkGray
        } else {
            Write-Host "    Import not verified. Your backup is safe at: $tar" -ForegroundColor Red
            Write-Host "    Re-import with: wsl --import $($d.Name) `"$destDir`" `"$tar`"" -ForegroundColor Yellow
        }
    } catch { Write-Host "    ERROR: $($_.Exception.Message)" -ForegroundColor Red; Write-Log "RELOCATE FAILED $($d.Name): $($_.Exception.Message)" }
}

# ===========================================================================
# BANNER + PREFLIGHT
# ===========================================================================
Clear-Host
$modeStr = if ($Report) { "REPORT" } elseif ($Auto) { "AUTO" } else { "INTERACTIVE" }
Write-Host ""
Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   CLAUDE + WSL LEAN-SPACE CLEANUP  -  v3                  |" -ForegroundColor Cyan
Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
Write-Host ("   Mode: {0}   Start free: {1} GB   Target: {2} GB" -f $modeStr,$startFree,$MinFreeGB) -ForegroundColor DarkGray
Write-Host ""
Write-Log "=== CLEANUP v3 | mode=$modeStr | start=$startFree GB ==="

if (-not $Report) {
    if (@(Get-Process -Name 'Claude' -EA SilentlyContinue).Count -gt 0) {
        if ($Auto -or $StopApps) { Stop-ClaudeAndWsl }
        else {
            Write-Host "  Claude Desktop is running (its files are locked). Close it + shut WSL now? [Y/n]: " -ForegroundColor Yellow -NoNewline
            if ((Read-Host) -notmatch '^[Nn]$') { Stop-ClaudeAndWsl } else { Write-Host "  Continuing; locked items will be skipped." -ForegroundColor DarkGray }
        }
    } else { try { & wsl --shutdown 2>&1 | Out-Null } catch { } }
}
Write-Host ""

# ===========================================================================
# CLAUDE / COWORK TARGETS
# ===========================================================================
Write-Host "  == COWORK VM BUNDLES =====================================" -ForegroundColor Magenta
Invoke-Cleanup "VM compressed download cache (.zst)" `
    @("$vmDir\rootfs.vhdx.zst","$vmDir\initrd.zst","$vmDir\vmlinuz.zst","$vmDir\.rootfs.vhdx.zst.origin","$vmDir\.initrd.zst.origin","$vmDir\.vmlinuz.zst.origin") `
    'SAFE' "Compressed installers; extracted copies are in use. Re-downloaded only on full reinstall."
Invoke-Cleanup "Cowork VM swap file (swap.vhdx)" @("$local\Temp\swap.vhdx") `
    'SAFE' "VM scratch swap. Recreated next launch. Only removable while Claude is closed."
Invoke-Cleanup "VM persistent state (sessiondata.vhdx)" @("$vmDir\sessiondata.vhdx") `
    'REVIEW' "Resets in-VM pip/npm installs only. Projects/chats/WSL untouched."

Write-Host ""
Write-Host "  == CLAUDE DESKTOP CACHES =================================" -ForegroundColor Magenta
$chromiumSubs = @("Cache","Code Cache","GPUCache","DawnGraphiteCache","DawnWebGPUCache","ShaderCache","blob_storage","Crashpad","Session Storage","Network","sentry","Partitions")
$uwpCachePaths = @(); foreach ($s in $chromiumSubs) { $uwpCachePaths += "$uwpBase\$s" }
Invoke-Cleanup "UWP Chromium/Electron caches" $uwpCachePaths 'SAFE' "Auto-regenerated on launch."
Invoke-Cleanup "UWP app logs" @("$uwpBase\logs") 'SAFE' "Recreated on launch."
$electronRoots = @("$roaming\Claude","$local\Claude","$local\Claude Nest","$local\Claude Nest-3p","$local\Claude-3p","$local\claude-cli-nodejs")
$electronPaths = @(); foreach ($root in $electronRoots) { foreach ($s in @("Cache","Code Cache","GPUCache","ShaderCache","DawnGraphiteCache","blob_storage","logs","Crashpad","Session Storage","Network")) { $electronPaths += "$root\$s" } }
Invoke-Cleanup "Roaming/Local Electron caches (all Claude variants)" $electronPaths 'SAFE' "Auto-regenerated."

Write-Host ""
Write-Host "  == OLD VERSIONS & SANDBOXES ==============================" -ForegroundColor Magenta
foreach ($sub in @("claude-code","claude-code-vm")) {
    $dir = "$uwpBase\$sub"
    if (Test-Path $dir) {
        $vers = @(Get-ChildItem $dir -Directory -Force -EA SilentlyContinue | Sort-Object Name)
        if ($vers.Count -gt 1) {
            $latest = $vers[-1].Name
            $old = @($vers | Where-Object { $_.Name -ne $latest } | ForEach-Object { $_.FullName })
            if ($old.Count -gt 0) { Invoke-Cleanup "Old $sub versions (newest $latest kept)" $old 'SAFE' "Only newest is used." }
        }
    }
}
Invoke-Cleanup "UWP local-agent-mode-sessions (stale mirror)" @("$uwpBase\local-agent-mode-sessions") 'REVIEW' "Mirror of your live sessions; usually stale."
Invoke-Cleanup "Claude Nest / Nest-3p / Claude-3p sandboxes" @("$local\Claude Nest","$local\Claude Nest-3p","$local\Claude-3p") 'SAFE' "Sandbox shells, not your main app."

Write-Host ""
Write-Host "  == PROFILE & VSCODE LEFTOVERS ============================" -ForegroundColor Magenta
Invoke-Cleanup ".claude old config backups" @("$user\.claude\backups") 'SAFE' "Redundant backups of ~/.claude.json."
Invoke-Cleanup ".claude-server-commander tool log" @("$user\.claude-server-commander\claude_tool_call.log") 'SAFE' "Log only."
Invoke-Cleanup ".claude-server-commander (MCP server)" @("$user\.claude-server-commander") 'REVIEW' "Delete only if unused; reinstalls on demand."
$extDir = "$user\.vscode\extensions"
if (Test-Path $extDir) {
    $exts = @(Get-ChildItem $extDir -Directory -Filter "anthropic.claude-code-*" -Force -EA SilentlyContinue | Sort-Object Name)
    if ($exts.Count -gt 1) {
        $latest = $exts[-1].Name
        $oldExt = @($exts | Where-Object { $_.Name -ne $latest } | ForEach-Object { $_.FullName })
        if ($oldExt.Count -gt 0) { Invoke-Cleanup "Old VSCode claude-code extensions (newest $latest kept)" $oldExt 'SAFE' "VSCode runs newest only." }
    }
}
$vsixDir = "$roaming\Code\CachedExtensionVSIXs"
if (Test-Path $vsixDir) {
    $vsix = @(Get-ChildItem $vsixDir -Directory -Filter "anthropic.claude-code-*" -Force -EA SilentlyContinue | ForEach-Object { $_.FullName })
    $vsix += "$vsixDir\.trash"
    Invoke-Cleanup "VSCode VSIX installer cache (Claude)" @($vsix) 'SAFE' "Cached installers; not needed to run."
}

Write-Host ""
Write-Host "  == COWORK SESSION DATA (recent <=3d protected) ==========" -ForegroundColor Magenta
$coworkSessions = "$roaming\Claude\local-agent-mode-sessions"
if (Test-Path $coworkSessions) {
    $auditFiles = @(Get-ChildItem -Path $coworkSessions -Recurse -Filter "audit.jsonl" -Force -EA SilentlyContinue | Where-Object { $_.Length -gt 1MB })
    if ($auditFiles.Count -gt 0) {
        $auditTotal = ($auditFiles | Measure-Object Length -Sum).Sum
        Invoke-Cleanup "Large audit.jsonl logs ($($auditFiles.Count) files, $(Format-Bytes $auditTotal))" @($auditFiles | ForEach-Object { $_.FullName }) 'SAFE' "Debug logs; memory/outputs unaffected."
    }
    $topDirs = @(Get-ChildItem -Path $coworkSessions -Directory -EA SilentlyContinue | Where-Object { $_.Name -ne 'skills-plugin' } | Sort-Object LastWriteTime)
    foreach ($dir in $topDirs) {
        $age = ((Get-Date) - $dir.LastWriteTime).Days
        if ($age -le 3) { Write-Host "  -> Protected (recent, ${age}d): $($dir.Name.Substring(0,[Math]::Min(24,$dir.Name.Length)))..." -ForegroundColor DarkGray; continue }
        $tier = if ($age -gt 14) { 'SAFE' } else { 'REVIEW' }
        Invoke-Cleanup "Cowork session (${age}d old) $($dir.Name.Substring(0,[Math]::Min(14,$dir.Name.Length)))..." @($dir.FullName) $tier "Old session uploads/outputs/cache."
    }
} else { Write-Host "  -> Cowork sessions folder not present." -ForegroundColor DarkGray }

# ===========================================================================
# WSL2 (relocate OR clean+compact in place)
# ===========================================================================
Write-Host ""
Write-Host "  == WSL2 DISTROS ==========================================" -ForegroundColor Magenta
$distros = @(Get-WslDistros)
if ($distros.Count -eq 0) {
    Write-Host "  -> No WSL2 distros with an ext4.vhdx found." -ForegroundColor DarkGray
} else {
    foreach ($d in $distros) { Write-Host ("  Found: {0,-20} on disk {1}  ({2})" -f $d.Name,(Format-Bytes (Get-OnDiskSize $d.Vhd)),$d.Base) -ForegroundColor Yellow }
    if ($RelocateWslTo) {
        foreach ($d in $distros) { Move-WslToDrive $d $RelocateWslTo }
    } else {
        $run = $true
        if (-not ($Auto -or $Report)) {
            Write-Host "  Clean + compact the distro(s) in place? (use -RelocateWslTo D to move them) [Y/n]: " -ForegroundColor Yellow -NoNewline
            $run = ((Read-Host) -notmatch '^[Nn]$')
        }
        if ($run -and -not $Report) { foreach ($d in $distros) { Optimize-WslDisk $d } }
        elseif ($Report) { Write-Host "  [REPORT] would clean caches + sparse + trim each distro." -ForegroundColor Green }
    }
}

# ===========================================================================
# OPTIONAL USER CACHES (never auto)
# ===========================================================================
Write-Host ""
Write-Host "  == OPTIONAL USER CACHES (not system folders) =============" -ForegroundColor DarkCyan
Invoke-Cleanup "AI models - Ollama" @("$user\.ollama\models") 'REVIEW' "Re-downloadable."
Invoke-Cleanup "AI model - Qwen3.5-9B" @("$user\Qwen3.5-9B") 'REVIEW' "Re-downloadable."
Invoke-Cleanup "AI caches - HuggingFace / Torch" @("$user\.cache\huggingface","$local\huggingface","$user\.cache\torch") 'REVIEW' "Re-downloadable."
Invoke-Cleanup "Dev tool download caches (npm/pip/yarn/conda)" @("$roaming\npm-cache","$local\pip\Cache","$local\Yarn\Cache","$user\miniconda3\pkgs") 'REVIEW' "Download caches; installed packages untouched."
Invoke-Cleanup "Browser caches (Edge/Chrome)" @("$local\Microsoft\Edge\User Data\Default\Cache","$local\Google\Chrome\User Data\Default\Cache") 'REVIEW' "Rebuilt automatically; close browser first."

# ===========================================================================
# SUMMARY
# ===========================================================================
$endFree = Get-FreeGB
Write-Host ""
Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
if ($Report) { Write-Host ("  |  REPORT: would free up to {0,-30}|" -f (Format-Bytes $script:WouldFree)) -ForegroundColor Cyan }
else         { Write-Host ("  |  FREED THIS RUN: {0,-39}|" -f (Format-Bytes $script:Freed)) -ForegroundColor Cyan }
Write-Host ("  |  C: free  {0,6} GB  ->  {1,6} GB                   |" -f $startFree,$endFree) -ForegroundColor Cyan
Write-Host "  +----------------------------------------------------------+" -ForegroundColor Cyan
if (-not $Report) {
    if ($endFree -ge $MinFreeGB) { Write-Host ("  TARGET MET: {0} GB >= {1} GB." -f $endFree,$MinFreeGB) -ForegroundColor Green }
    else {
        Write-Host ("  BELOW TARGET: {0} GB < {1} GB." -f $endFree,$MinFreeGB) -ForegroundColor Yellow
        Write-Host "  Biggest lever: move WSL to another drive ->  .\CLEANUP_v3.ps1 -RelocateWslTo D" -ForegroundColor Yellow
    }
}
Write-Host "  Log: $LogFile" -ForegroundColor Green
Write-Log "=== v3 complete | freed=$(Format-Bytes $script:Freed) | end=$endFree GB ==="
