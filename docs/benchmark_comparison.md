# Benchmark: RAG Runtime Kernel vs Alternatives

> Lightweight positioning comparison — not a deep research paper.

## What Each System Is

| System | Type | Core Idea |
|---|---|---|
| **RAG Runtime Kernel** | Specification + optional Python runtime | Two tiers. **Tier 1 (Simple):** paste one markdown spec, the LLM self-enforces it — no install. **Tier 2 (Enforced):** the `rag_kernel` Python runtime intercepts every state change, so state management runs off the LLM and is formally verified. |
| **Claude Code (native)** | CLI tool + CLAUDE.md | Per-session coding agent. Memory via CLAUDE.md files + auto memory notes. Stateless by design — each session starts fresh, reconstructs from files. |
| **lean-ctx** | Rust binary + MCP server | Context compression layer. Sits between AI tool and LLM, compresses file reads + shell output by 60–99%. Focused on token reduction, not state management. |
| **Karpathy LLM Wiki** | Architectural pattern | "Compile once, query forever." LLM builds a persistent wiki from raw sources. Three layers: Sources → Wiki → Schema. Philosophy over implementation. |

## Comparison Matrix

| Dimension | RAG Runtime Kernel | Claude Code | lean-ctx | LLM Wiki |
|---|---|---|---|---|
| **Memory persistence** | Full: HOT/COLD with atomic writes, WAL, crash recovery, backup rotation | Partial: CLAUDE.md + auto memory. No crash recovery. Manual curation needed monthly | None: compresses I/O, doesn't persist state | Pattern only: relies on the LLM or external tooling to implement persistence |
| **State machine** | Explicit: BOOTING→READY→WORKING→CHECKPOINTING→CLOSING with RECOVERY path | None: simple while-loop agent with permission gates | None | None |
| **Token approach** | Structural, not a single percentage: in Tier 2 the entire state layer (bootstrap, validation, persistence, recovery) runs in Python at **0 LLM tokens**; HOT-only boot keeps active context lean. We do not publish a measured % reduction. | Depends on CLAUDE.md size. Auto memory grows unbounded without curation | 60–99% reduction (best in class for raw I/O compression). Cached re-reads: ~13 tokens | Depends on wiki quality. Good wiki = massive reduction. Bad wiki = noise |
| **Cross-platform** | Yes: works in Claude Projects, ChatGPT, any LLM. Spec is the invariant | Claude Code only (CLI). Some community ports (OpenClaw, Gemini CLI) | Editor-focused: Cursor, Claude Code, Copilot, Windsurf, Codex, Gemini | Platform-agnostic pattern. Implementations vary |
| **External dependencies** | Tier 1: zero — single markdown file. Tier 2: Python 3.12-3.14 only | Node.js + Claude Code CLI | Rust binary. Single install, zero runtime deps | Varies by implementation. Core pattern = none |
| **Failure resilience** | WAL replay, .bak rotation, RECOVERY state, fallback tool chains | File-history checkpoints for --rewind-files. Compact boundaries. No WAL | N/A — not a persistence system | No built-in resilience |
| **Conflict handling** | Explicit conflict ledger. Both sources preserved. Never silently merged | No structured conflict handling | N/A | No built-in conflict handling |
| **Multi-account safety** | Session identity tagging, write collision detection, anti-corruption guards | Per-project isolation via CLAUDE.md. No cross-session collision detection | N/A | N/A |
| **Scalability** | COLD partitioning + sub-partitioning with integrity-preserving chopping protocol | CLAUDE.md stays small by convention. Auto memory needs manual pruning | Built for scale — compression handles large codebases well | Wiki scaling is an open problem (Karpathy acknowledges this) |
| **Agent autonomy** | Self-enforcing: all rules apply without external wrapper. Proposal→Validate→Commit | High autonomy within session. No cross-session state enforcement | Not an agent — a compression layer | Not an agent — an architecture |
| **Audit trail** | Full: every state transition, decision, and conflict logged in WAL | Append-only JSONL transcripts + prompt history | Session metrics + gain tracking | None built-in |
| **Setup effort** | Tier 1: paste the spec, done. Tier 2: copy `rag_kernel/`, run one `init` command | npm install + configure CLAUDE.md | curl install, lean-ctx setup | Read the gist. Build your own implementation |

## Where Each Wins

**RAG Runtime Kernel wins when:** You need structured, validated, cross-session state persistence across any LLM platform. Projects with legal, financial, or compliance requirements where audit trails and conflict tracking matter. Long-running multi-session projects where "the next session must reconstruct full state from filesystem alone."

**Claude Code wins when:** You're a developer doing code-focused work within the Claude ecosystem. Session-level autonomy with tool access. The CLAUDE.md pattern is simple and effective for coding workflows.

**lean-ctx wins when:** Token cost is the primary concern. Large codebases with noisy CLI output. You want compression without changing your workflow. Pairs well with any agent system (including RTK).

**LLM Wiki wins when:** You're building a knowledge base that compounds over time. Research-oriented workflows. The "compile once, query forever" pattern aligns with your use case.

## Key Differentiators — RAG Runtime Kernel

1. **Only system that enforces a deterministic state machine on LLM workflows** — no other tool provides formal transition guards between session states
2. **Only system that works across both Claude and ChatGPT** with the same spec file — no platform lock-in
3. **Only system with atomic write protocol + WAL + backup rotation** for LLM-managed state — enterprise-grade persistence from a single markdown file
4. **Runs at either end of the spectrum** — Tier 1 is a zero-install markdown spec; Tier 2 is a formally-verified Python runtime that enforces it. Same RAG files, no rewrite to graduate.
5. **State management runs off the LLM (Tier 2)** — bootstrap, validation, persistence, and recovery execute as deterministic code at zero reasoning-token cost, not as model proposals
6. **Conflict ledger is unique** — no other system explicitly tracks and preserves disagreements between sources

## Complementary, Not Competing

lean-ctx + RTK is a natural pairing: lean-ctx compresses the I/O layer while RTK manages the state layer. Claude Code's CLAUDE.md pattern inspired parts of RTK's operating_protocol. The LLM Wiki's "compile once" philosophy is exactly what RTK's COLD partitioning implements at a structural level.
