"""Find a Cowork / Claude-desktop session on THIS host by searching for text.

WHY THIS EXISTS. S211 was told "find the sibling projects' last Cowork sessions,
go in and read them" and burned most of a session re-deriving where those
sessions live, twice looking in the wrong place and once with a wrong file
filter. The operator's instruction was explicit: once you find it, WRITE THE
METHOD DOWN so nobody steps on this rake again. This is that write-down, as a
runnable script rather than prose, because prose does not execute.

WHERE SESSIONS ACTUALLY LIVE ON WINDOWS — measured 2026-09-17, four stores, and
they are NOT interchangeable:

  1. CLI sessions (the `claude` terminal client):
       %USERPROFILE%\\.claude\\projects\\<cwd-slug>\\<session-uuid>.jsonl
     Full transcripts, plain JSONL, greppable. ONLY holds sessions started from
     a terminal in that working directory. A Cowork session is NOT here.

  2. Cowork sessions, legacy store (up to 2026-08-16 on this host):
       %APPDATA%\\Claude\\local-agent-mode-sessions\\<account>\\<org>\\
           local_<session-uuid>\\audit.jsonl
     232 transcripts here, multi-MB, plain JSONL, greppable. THIS is the store
     to search for anything older than mid-August.

  3. Cowork sessions, current store:
       %APPDATA%\\Claude\\claude-code-sessions\\<account>\\<org>\\
           local_<session-uuid>.json
     ~30 KB each -- session CONFIG (MCP toggles, settings), not transcript body.
     Do not mistake these for transcripts, as S211 first did.

  4. claude.ai project conversations (what the desktop app shows under
     Projects > <name>):
       %APPDATA%\\Claude\\IndexedDB\\https_claude.ai_0.indexeddb.blob\\**
     Binary blobs. Conversation NAME, uuid, account/org ids and the app-written
     SUMMARY are recoverable as UTF-8 fragments; the message BODY is compressed
     and is NOT readable by grepping. For the body use the host's session MCP
     (mcp__ccd_session_mgmt__*), which needs a declared transport.

THREE MISTAKES THIS SCRIPT EXISTS TO PREVENT, each one measured in S211:

  * `-Include *.json` does NOT match `.jsonl`. That single character hid all 232
    Cowork transcripts for most of a session.
  * Searching for the PROJECT name finds nothing. Conversations are titled by
    SUBJECT -- the session wanted was "Sarah's response and next steps", inside
    a project called IRCC_REBUILT. Search for people, files and phrases, not for
    the project.
  * `[System.IO.File]::ReadAllBytes` throws on the app's live leveldb files
    because the running client holds a lock. Open with FileShare.ReadWrite and
    skip what still refuses, or one locked file aborts the whole sweep.

RUN IT (Windows interpreter -- WSL cannot stat %APPDATA%\\Claude, see
WSL-CANNOT-STAT-APPDATA):

    python scripts/find_cowork_session.py "Sarah"
    python scripts/find_cowork_session.py "IRCC" --context 400 --limit 5
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: (label, root, glob) for every store that can hold a session, in the order a
#: searcher should read them: transcripts first, metadata-only last.
STORES = (
    ("cli-session", Path.home() / ".claude" / "projects", "**/*.jsonl"),
    ("cowork-legacy", None, "local-agent-mode-sessions/**/audit.jsonl"),
    ("cowork-config", None, "claude-code-sessions/**/*.json"),
    ("claude-ai-blob", None, "IndexedDB/https_claude.ai_0.indexeddb.blob/**/*"),
)

#: Stores whose hits carry only a title/summary, never the message body.
METADATA_ONLY = {"cowork-config", "claude-ai-blob"}

MAX_BYTES = 40 * 1024 * 1024


def _appdata_claude() -> "Path | None":
    base = os.environ.get("APPDATA")
    if not base:
        return None
    p = Path(base) / "Claude"
    return p if p.is_dir() else None


def _read(path: Path) -> "str | None":
    """Read a file the desktop client may be holding open. -> text | None."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        # FileShare.ReadWrite equivalent: plain open() on Windows already shares
        # read; the failure mode is a hard lock, which we simply skip.
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def iter_candidates():
    appdata = _appdata_claude()
    for label, root, pattern in STORES:
        base = root if root is not None else appdata
        if base is None or not Path(base).is_dir():
            continue
        for p in Path(base).glob(pattern):
            if p.is_file() and p.stat().st_size:
                yield label, p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("query", help="text to find (case-insensitive substring)")
    ap.add_argument("--context", type=int, default=250,
                    help="characters of context around each hit (default 250)")
    ap.add_argument("--limit", type=int, default=20,
                    help="max files to report (default 20)")
    ap.add_argument("--hits-per-file", type=int, default=1,
                    help="max excerpts per file (default 1)")
    a = ap.parse_args(argv)

    needle = a.query.lower()
    found = 0
    for label, path in iter_candidates():
        if found >= a.limit:
            break
        text = _read(path)
        if text is None:
            continue
        low = text.lower()
        i = low.find(needle)
        if i < 0:
            continue
        found += 1
        stamp = path.stat().st_mtime
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
        note = "  [metadata only -- body is compressed]" if label in METADATA_ONLY else ""
        print(f"\n===== [{label}] {when}  {path.stat().st_size:>10} B{note}")
        print(f"      {path}")
        shown = 0
        while i >= 0 and shown < a.hits_per_file:
            s = max(0, i - a.context)
            excerpt = text[s:s + a.context * 2].replace("\r", " ")
            print("      ---")
            print("      " + excerpt.replace("\n", "\n      "))
            shown += 1
            i = low.find(needle, i + len(needle))

    if not found:
        print(f"no store holds {a.query!r}. Checked: "
              + ", ".join(label for label, _, _ in STORES))
        return 1
    print(f"\n{found} file(s) matched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
