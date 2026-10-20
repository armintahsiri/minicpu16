#!/usr/bin/env python3
"""Fuzz-program diversity audit.

Hashes every generated program source (normalised: comments removed, whitespace
collapsed) and reports unique vs total overall and per generator mode, for the
quick (n=24) and full (n=300) fuzz configurations used by tools/rtl_fuzz.py.

The audit is deliberately independent of the simulator: it answers whether two
different seeds produce the same program text, which is the root cause of the
Phase 5A observation that several modes had identical cycles/stalls.

Usage:
  python3 tools/fuzz_diversity.py
  python3 tools/fuzz_diversity.py --n 300 --json build/diversity.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import random_programs  # noqa: E402

# Must match tools/rtl_fuzz.py MODES order, because the fuzzer picks the mode
# from the case index while the seed varies.
MODES = ["hazards", "straight", "loop", "calls", "coverage", "mmio_writes", "fwd",
         "store_load"]


def normalise(text: str) -> str:
    """Canonical form used for the uniqueness hash.

    Comments and blank lines are dropped; all whitespace runs collapse to one
    space.  Labels keep their names, so two programs that differ only in a
    label are still distinct: register allocation and control flow matter.
    """
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        line = re.sub(r"\s+", " ", line)
        out.append(line)
    return "\n".join(out)


def digest(text: str) -> str:
    return hashlib.sha256(normalise(text).encode()).hexdigest()


def audit(n: int, seed_base: int = 0) -> Dict[str, object]:
    per_mode_total: Dict[str, int] = defaultdict(int)
    per_mode_unique: Dict[str, set] = defaultdict(set)
    all_hashes: set = set()
    groups: Dict[str, List[int]] = defaultdict(list)
    for i in range(n):
        seed = seed_base + i
        mode = MODES[i % len(MODES)]
        gp = random_programs.generate(seed, mode)
        h = digest(gp.source)
        per_mode_total[mode] += 1
        per_mode_unique[mode].add(h)
        all_hashes.add(h)
        groups[h].append(seed)
    rows = []
    for mode in MODES:
        total = per_mode_total[mode]
        uniq = len(per_mode_unique[mode])
        rows.append({
            "mode": mode,
            "total": total,
            "unique": uniq,
            "pct": round(100.0 * uniq / total, 1) if total else 0.0,
        })
    duplicates = {h: s for h, s in groups.items() if len(s) > 1}
    dup_list = [
        {"hash": h[:16], "count": len(s), "seeds": s[:12]}
        for h, s in sorted(duplicates.items(), key=lambda kv: -len(kv[1]))[:20]
    ]
    return {
        "n": n,
        "seed_base": seed_base,
        "total": n,
        "unique": len(all_hashes),
        "pct": round(100.0 * len(all_hashes) / n, 1) if n else 0.0,
        "per_mode": rows,
        "duplicate_groups": len(duplicates),
        "largest_duplicate_groups": dup_list,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Fuzz program diversity audit")
    p.add_argument("--n", type=int, default=None,
                   help="number of generated programs (default: quick 24 and full 300)")
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--json", metavar="FILE")
    p.add_argument("--min-overall", type=float, default=98.0)
    p.add_argument("--min-per-mode", type=float, default=90.0)
    p.add_argument("--no-assert", action="store_true")
    ns = p.parse_args(argv)

    if ns.n is None:
        reports = [audit(24, ns.seed_base), audit(300, ns.seed_base)]
        for label, rep in zip(("quick n=24", "full n=300"), reports):
            print("== %s ==" % label)
            print("  overall: %d/%d unique (%.1f%%)" %
                  (rep["unique"], rep["total"], rep["pct"]))
            for row in rep["per_mode"]:
                print("    %-14s %3d/%3d unique (%5.1f%%)" %
                      (row["mode"], row["unique"], row["total"], row["pct"]))
            print("  duplicate groups: %d" % rep["duplicate_groups"])
            for g in rep["largest_duplicate_groups"][:5]:
                print("    x%-3d seeds %s" % (g["count"], g["seeds"]))
            print()
        target = reports[1]
    else:
        target = audit(ns.n, ns.seed_base)
        print("overall: %d/%d unique (%.1f%%)" %
              (target["unique"], target["total"], target["pct"]))
        for row in target["per_mode"]:
            print("  %-14s %3d/%3d unique (%5.1f%%)" %
                  (row["mode"], row["unique"], row["total"], row["pct"]))
        print("duplicate groups: %d" % target["duplicate_groups"])
        for g in target["largest_duplicate_groups"][:10]:
            print("  x%-3d seeds %s" % (g["count"], g["seeds"]))

    if ns.json:
        Path(ns.json).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.json).write_text(json.dumps(target, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8", newline="\n")

    if ns.no_assert:
        return 0
    bad = target["pct"] < ns.min_overall
    for row in target["per_mode"]:
        if row["total"] and row["pct"] < ns.min_per_mode:
            bad = True
    if bad:
        print("DIVERSITY ASSERTION FAILED: require >= %.0f%% overall and >= %.0f%% per mode"
              % (ns.min_overall, ns.min_per_mode), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
