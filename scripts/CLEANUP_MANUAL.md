# CLEANUP_v3 - Claude + WSL Lean-Space Cleanup

A PowerShell utility that reclaims disk space from the only two things that
actually clog this machine: **Claude Desktop / Cowork** and **WSL (Ubuntu)**.
It does NOT touch Windows system folders (no C:\Windows, DNS, WinSxS, etc).

---

## Quick start

1. Right-click Start - "Terminal" or "Windows PowerShell".
2. Go to the folder:
       cd "C:\Users\pakhol\Desktop\CLEANUP UTL SCRIPT"
3. See what it *would* do, changing nothing:
       powershell -ExecutionPolicy Bypass -File .\CLEANUP_v3.ps1 -Report

The script auto-elevates to Administrator (UAC prompt) and, if Claude Desktop
is open, offers to close it so locked files can be cleaned.

> Run it from a normal PowerShell window - NOT from inside Claude/Cowork,
> because it may close the Claude app.

---

## The commands you'll actually use

| Command | What it does |
|---|---|
| `.\CLEANUP_v3.ps1 -Report` | Dry run. Lists everything reclaimable. Deletes nothing. |
| `.\CLEANUP_v3.ps1` | Interactive. Asks y/N before each item. |
| `.\CLEANUP_v3.ps1 -Auto` | Unattended. Clears only GREEN/safe items, skips REVIEW. Good for scheduling. |
| `.\CLEANUP_v3.ps1 -DeepWslClean` | Also runs `conda clean --all` inside Ubuntu before trimming (frees GBs of cache; keeps all envs). |
| `.\CLEANUP_v3.ps1 -RelocateWslTo D` | Moves WSL distro(s) to D:. Frees C: permanently AND rebuilds a compact disk. |
| `.\CLEANUP_v3.ps1 -MinFreeGB 20` | Sets the free-space target for the pass/fail summary (default 15). |

Flags combine, e.g.  `.\CLEANUP_v3.ps1 -Auto -DeepWslClean -MinFreeGB 20`

---

## How it treats your stuff (tiers)

- **SAFE (green):** caches, logs, old versions - auto-regenerated. Cleared in `-Auto`.
- **REVIEW (yellow):** things you might still want (AI models, browser cache,
  old sessions, the MCP server). NEVER auto-deleted; always asks.
- Cowork sessions used in the **last 3 days are always protected.**

Nothing about your projects, chats, conda environments, or WSL data is deleted.

---

## The WSL lesson baked into v3

A WSL disk (`ext4.vhdx`) grows but does not shrink on its own. It only shrinks
if files are **deleted inside it first**, then the disk is trimmed. v3 does this
in the correct order:

1. clean caches **inside** the distro (apt/npm/pip/journal; +conda with `-DeepWslClean`)
2. shut WSL down, **enable sparse**, then **fstrim** (this order matters)
3. compact in place **only if** your WSL supports `--manage --optimize` (WSL 2.5+)

If your WSL is older (no `--optimize`), in-place compaction is limited - the
clean fix is to re-import the disk, which `-RelocateWslTo` does for you.

Tip: `wsl --update` brings the newer `--optimize` command if you want in-place
compaction without relocating.

---

## Your current setup (after the June 2026 cleanup)

- **Ubuntu was moved to `D:\WSL\Ubuntu`** (it was eating ~15 GB on C:).
  Your tmux-mcp / wsl-exec / conda all still work - same distro name, default
  user `pakhol` restored.
- **C: free went from ~13 GB to ~28 GB.** Sparse mode is enabled, so the WSL
  disk auto-reclaims freed space going forward.
- A one-time backup of the old Ubuntu may still sit at `D:\WSL\Ubuntu_export.tar`
  (or `ubuntu.tar`). Once you're confident everything works, you can delete it.

Because Ubuntu now lives on D:, you usually won't need to relocate again - run
`-Report` occasionally, and `-DeepWslClean` if WSL bulks up.

---

## Safety notes

- Always start with `-Report`.
- `-RelocateWslTo` keeps the exported `.tar` until the new copy is verified, so
  your data is never without a copy.
- It will not touch Windows system files, your projects, or your conda envs.

---

## Scheduling & the `-Auto` caveat (IMPORTANT)

`-Auto` closes Claude Desktop to unlock its cache files. If the script ran
*in-process* (inside the Claude/Cowork session that launched it), it would kill
the very session that started it. So the scheduled task launches the script
**detached** — the cleanup finishes in the background even as Claude closes;
you just reopen Claude after.

A paused weekly task named **claude-wsl-disk-cleanup** is already set up
(Sunday 3am, **disabled** so it never fires on its own). Start it manually from
the **Scheduled** panel in Claude. It runs, detached:

    Start-Process powershell ... -File "...\CLEANUP_v3.ps1" -Auto -MinFreeGB 20

then reads the newest `CLEANUP_*.log` on your Desktop to report freed space.

If you instead run `-Auto` yourself from a normal PowerShell window (not inside
Claude), letting it close Claude directly is fine — just reopen afterward.
