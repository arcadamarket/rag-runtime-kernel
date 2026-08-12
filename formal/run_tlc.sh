#!/usr/bin/env bash
# Run the TLA+ model checks with TLC's scratch space on LOCAL disk.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# The repository lives on the Windows filesystem, which WSL reaches through the
# 9p mount at /mnt/c. That is fine for source. It is NOT fine for TLC's working
# set: TLC spills its fingerprint set (MSBDiskFPSet) and its state queue
# (DiskStateQueue) to disk *in the current directory* as it explores, and on 9p
# those writes dominate everything else.
#
# Measured, S198: RAGKernel (389,522 states generated / 168,520 distinct) ran
# for more than twenty minutes from /mnt/c and had to be killed. The same model,
# same jar, same machine, run from local ext4: about two minutes. Nothing was
# wrong with the spec. It just looked hung, which is the worst failure mode a
# verification tool can have — it teaches you to stop running it.
#
# So: copy the .tla/.cfg to a local scratch dir, run there, report. The specs
# stay in the repo where they belong; only TLC's scratch moves.
#
# USAGE
#   ./run_tlc.sh                 # every spec: proof configs + counterfactuals
#   ./run_tlc.sh SessionIdShape  # one spec (both of its configs)
#
# READING THE OUTPUT
#   PROOF  ... PASS   the invariants hold
#   NAIVE  ... REFUTED the naive rule is unsound, as intended
#
# A NAIVE CONFIG THAT PASSES IS A REGRESSION, not good news: it means the guard
# that config exists to refute has been weakened back to the naive form. The
# script exits non-zero on that, and on any proof failure.

set -uo pipefail

FORMAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAR="${TLA2TOOLS_JAR:-$HOME/tla2tools.jar}"
WORKERS="${TLC_WORKERS:-4}"
TIMEOUT="${TLC_TIMEOUT:-1200}"
SCRATCH="$(mktemp -d /tmp/tlc.XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

if [[ ! -f "$JAR" ]]; then
    echo "REFUSE: tla2tools.jar not found at $JAR" >&2
    echo "  Set TLA2TOOLS_JAR, or fetch it:" >&2
    echo "  curl -L -o ~/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar" >&2
    exit 2
fi

case "$SCRATCH" in
    /mnt/*) echo "REFUSE: scratch dir landed on a mounted filesystem ($SCRATCH)." >&2
            echo "  That is the exact condition this script exists to avoid." >&2
            exit 2 ;;
esac

SPECS=()
if [[ $# -gt 0 ]]; then
    SPECS=("$@")
else
    for f in "$FORMAL_DIR"/*.tla; do
        [[ -e "$f" ]] || continue
        SPECS+=("$(basename "$f" .tla)")
    done
fi

# INSPECTED-COUNT-DISCLOSURE (E-130): state the denominator. A verification
# runner that silently checks a subset of the specs and then prints "everything
# holds" is the same defect this project already banked once — a recognisable
# subset substituted for an enumerated set. So: count the specs on disk, count
# the ones about to run, and refuse if they disagree.
on_disk=0
for f in "$FORMAL_DIR"/*.tla; do [[ -e "$f" ]] && on_disk=$((on_disk + 1)); done
if [[ $# -eq 0 && ${#SPECS[@]} -ne $on_disk ]]; then
    echo "REFUSE: found $on_disk spec(s) on disk but enumerated ${#SPECS[@]}." >&2
    exit 2
fi
if [[ ${#SPECS[@]} -eq 0 ]]; then
    echo "REFUSE: no specs to check in $FORMAL_DIR" >&2
    exit 2
fi

configs_run=0
failures=0
echo "Checking ${#SPECS[@]} spec(s) from $FORMAL_DIR (scratch: $SCRATCH)"
echo
printf '%-32s %-8s %-9s %s\n' SPEC KIND RESULT DETAIL
printf '%-32s %-8s %-9s %s\n' -------------------------------- -------- --------- ------

for spec in "${SPECS[@]}"; do
    for cfg in "$spec" "${spec}_naive"; do
        [[ -f "$FORMAL_DIR/$cfg.cfg" ]] || continue
        [[ "$cfg" == *_naive ]] && kind=NAIVE || kind=PROOF

        # ONE SCRATCH DIR PER CONFIG, not one for the whole run. Sharing a
        # directory lets TLC find a previous config's `states/` metadir and take
        # a recovery path instead of a fresh check. Caught live: with a shared
        # dir, TransportProjectionGate produced no parseable output at all right
        # after RAGKernel's 168k-state run, and this script reported BROKEN for a
        # spec that passes cleanly in isolation. A verification runner that
        # invents failures is worse than none.
        run_dir="$SCRATCH/$cfg"
        mkdir -p "$run_dir"
        cp "$FORMAL_DIR/$spec.tla" "$FORMAL_DIR/$cfg.cfg" "$run_dir"/

        out="$(cd "$run_dir" && timeout "$TIMEOUT" java -XX:+UseParallelGC -jar "$JAR" \
                 -workers "$WORKERS" -config "$cfg.cfg" "$spec.tla" 2>&1)"
        rm -rf "$run_dir"

        if grep -qE 'Model checking completed. No error has been found' <<<"$out"; then
            verdict=PASS
        elif grep -qE 'is violated|is equal to FALSE' <<<"$out"; then
            verdict=REFUTED
        else
            verdict=BROKEN
        fi

        # TAIL, NOT HEAD. TLC prints periodic progress lines carrying a running
        # count, so `head -1` reports an early snapshot as if it were the total:
        # RAGKernel read "240 distinct states found" when the answer is 168,520.
        # In a project whose whole discipline is that a stated number must be a
        # measured one, a plausible wrong number is the worst kind.
        detail="$(grep -oE '[0-9]+ distinct states found' <<<"$out" | tail -1)"
        [[ -z "$detail" ]] && detail="$(grep -oE 'Invariant [A-Za-z_]+|invariant of [A-Za-z_]+' <<<"$out" | tail -1)"
        [[ -z "$detail" ]] && detail="(no parseable TLC output — rerun this config alone)"

        # A proof must PASS; a counterfactual must be REFUTED. Anything else fails.
        if { [[ $kind == PROOF && $verdict != PASS ]] || \
             { [[ $kind == NAIVE ]] && [[ $verdict != REFUTED ]]; }; }; then
            failures=$((failures + 1))
            verdict="!$verdict"
        fi

        configs_run=$((configs_run + 1))
        printf '%-32s %-8s %-9s %s\n' "$spec" "$kind" "$verdict" "$detail"
    done
done

echo
# VACUOUS PASS IS A FAILURE. "0 checked, everything holds" is true and useless,
# and it is what this script printed on its first outing when a path bug meant no
# config was ever found. A green light nobody earned is worse than a red one.
if (( configs_run == 0 )); then
    echo "REFUSE: 0 configs ran across ${#SPECS[@]} spec(s) — no .cfg files matched. Nothing was verified."
    exit 2
fi
if (( failures )); then
    echo "TLC: $configs_run config(s) checked, $failures did not behave as declared. See the ! markers."
    exit 1
fi
echo "TLC: $configs_run config(s) checked across ${#SPECS[@]} spec(s) — every proof holds and every counterfactual is refuted."
