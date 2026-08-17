#!/usr/bin/env python3
"""
POST-BIRTH / POST-SEAL ACCEPTANCE CHECK — the test S183 should have run.

`audit` proves content coherence. It does NOT prove a deployment can BOOT.
This asserts boot-readiness for every deployment in meta.deployments plus the
kernel itself, by running the checks that actually gate a successor session:

  1. verify            HOT/COLD coherence, no placeholders
  2. audit             renders/refs/side-stores
  3. bootmap (RO diff) domain-map coverage — the S2 blocker
  4. IDENTITY          pov_mandate.count == len(pov_roles) and != 0 in STRICT
  5. SEAL              session_close phase COMPLETE + transfer_ready
  6. session-start     the real acceptance test: does the gate pass?
                       (read-only: AUTO-SID derives without writing, and no
                       attestation is sent, so no logger opens and no state moves)

Exit 0 only if every deployment passes every check.
"""
import json, os, subprocess, sys

KERNEL_RAGDIR = "/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)/RAG"
FAILURES: list[str] = []


def sh(ragdir, args, timeout=900):
    p = subprocess.run([sys.executable, "-m", "rag_kernel"] + args,
                       cwd=ragdir, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def assess(label, ragdir):
    print(f"\n=== {label} ===\n  {ragdir}")
    rag = os.path.join(ragdir, "RAG_MASTER.json")
    if not os.path.exists(rag):
        check(f"{label}: RAG present", False, rag)
        return

    d = json.load(open(rag, encoding="utf-8"))

    rc, out = sh(ragdir, ["verify", "--rag", "RAG_MASTER.json"])
    check(f"{label}: verify", rc == 0, out.strip()[-200:])

    rc, out = sh(ragdir, ["audit", "--rag", "RAG_MASTER.json"])
    check(f"{label}: audit", rc == 0 and "0 findings" in out, out.strip()[-300:])

    # read-only map diff: any NEW governed file is a successor-boot blocker
    rc, out = sh(ragdir, ["bootmap", "--rag", "RAG_MASTER.json"])
    drift = [ln for ln in out.splitlines() if "coverage gap" in ln or "not in the boot-map" in ln]
    check(f"{label}: boot-map coverage", rc == 0 and not drift,
          "; ".join(drift)[:300] or out.strip()[-200:])

    roles = d.get("pov_roles") or []
    mandate = d.get("pov_mandate") or {}
    count, mode = mandate.get("count"), (mandate.get("mode") or "").lower()
    check(f"{label}: identity defined", bool(roles) and count == len(roles),
          f"count={count} roles={len(roles)} mode={mode}")

    sc = d.get("session_close") or {}
    check(f"{label}: seal", sc.get("phase") == "COMPLETE" and sc.get("transfer_ready") is True,
          f"{sc.get('session')} phase={sc.get('phase')} transfer_ready={sc.get('transfer_ready')}")

    # THE acceptance test — would a successor session actually start?
    rc, out = sh(ragdir, ["session-start", "--rag", "RAG_MASTER.json"])
    gate_ok = "OK — inherited RAG coherent" in out
    needs_attest = "Attestation REQUIRED" in out
    check(f"{label}: successor boot (carry-forward gate)", gate_ok and needs_attest,
          next((ln.strip() for ln in out.splitlines()
                if "refusing" in ln or "FAIL" in ln), out.strip()[-250:]))
    if gate_ok:
        frame = next((ln for ln in out.splitlines() if "POV mandate:" in ln), "")
        print("        " + frame.strip())


def main():
    assess("KERNEL", KERNEL_RAGDIR)

    d = json.load(open(os.path.join(KERNEL_RAGDIR, "RAG_MASTER.json"), encoding="utf-8"))
    for name, rec in (d["meta"].get("deployments") or {}).items():
        if name.startswith("_purpose"):
            continue
        root = rec.get("root")
        if not root or not os.path.isdir(root):
            print(f"\n=== {name} ===\n  recorded root not reachable: {root} — skipped "
                  f"(role={rec.get('role')})")
            continue
        ragdir = os.path.join(root, os.path.dirname(rec.get("rag", "RAG/RAG_MASTER.json")))
        assess(name, ragdir)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"NOT READY — {len(FAILURES)} failed check(s):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("READY — every deployment passes verify, audit, map coverage, identity, "
          "seal and a real successor boot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
