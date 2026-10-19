#!/usr/bin/env python3
"""Compare two MiniCPU-16 retirement traces and report the first divergence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

HEADER0 = "# minicpu16-retirement-trace v1"
HEADER1 = "# seq retire pc instr rd value memop memaddr memsize memdata taken target"
NFIELDS = 12


class TraceFormatError(Exception):
    pass


@dataclass(frozen=True)
class TraceLine:
    lineno: int
    raw: str
    fields: Tuple[str, ...]

    @property
    def seq(self) -> int:
        return int(self.fields[0])

    @property
    def retire(self) -> int:
        return int(self.fields[1])

    @property
    def pc(self) -> str:
        return self.fields[2]

    @property
    def instr(self) -> str:
        return self.fields[3]

    def key(self, ignore_cycles: bool = False) -> Tuple[str, ...]:
        if ignore_cycles:
            return (self.fields[0],) + self.fields[2:]
        return self.fields


def parse_trace(path: str) -> List[TraceLine]:
    lines: List[TraceLine] = []
    seen_header = False
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if s.startswith("#"):
                if s == HEADER0:
                    seen_header = True
                elif s == HEADER1:
                    pass
                else:
                    raise TraceFormatError("%s:%d: unknown header line %r" % (path, lineno, s))
                continue
            parts = tuple(s.split())
            if len(parts) != NFIELDS:
                raise TraceFormatError("%s:%d: expected %d fields, got %d" %
                                       (path, lineno, NFIELDS, len(parts)))
            if not seen_header:
                raise TraceFormatError("%s:%d: missing trace header" % (path, lineno))
            try:
                int(parts[0]); int(parts[1]); int(parts[2], 16); int(parts[3], 16)
                if parts[4] != "-" and not (parts[4].startswith("r") and 0 <= int(parts[4][1:]) <= 15):
                    raise ValueError("bad rd")
                for v in (parts[5], parts[7], parts[9], parts[11]):
                    if v != "-":
                        int(v, 16)
                if parts[6] not in ("-", "L", "S"):
                    raise ValueError("bad memop")
                if parts[8] not in ("-", "1", "2"):
                    raise ValueError("bad memsize")
                if parts[10] not in ("0", "1"):
                    raise ValueError("bad taken")
            except ValueError as e:
                raise TraceFormatError("%s:%d: bad field (%s): %r" % (path, lineno, e, s))
            lines.append(TraceLine(lineno, s, parts))
    if not seen_header:
        raise TraceFormatError("%s: missing trace header" % path)
    return lines


def diff_traces(a: Sequence[TraceLine], b: Sequence[TraceLine],
                ignore_cycles: bool = False, context: int = 3) -> Tuple[bool, str]:
    n = max(len(a), len(b))
    for i in range(n):
        if i >= len(a):
            return False, ("first divergence: left trace ended after %d records; "
                           "right has %d (first extra: line %d: %s)" %
                           (len(a), len(b), b[i].lineno, b[i].raw))
        if i >= len(b):
            return False, ("first divergence: right trace ended after %d records; "
                           "left has %d (first extra: line %d: %s)" %
                           (len(b), len(a), a[i].lineno, a[i].raw))
        ka = a[i].key(ignore_cycles)
        kb = b[i].key(ignore_cycles)
        if ka != kb:
            out = ["first divergence at record %d (left line %d, right line %d)" %
                   (i + 1, a[i].lineno, b[i].lineno)]
            names = ["seq", "retire", "pc", "instr", "rd", "value", "memop", "memaddr",
                     "memsize", "memdata", "taken", "target"]
            start = 1 if ignore_cycles else 0
            for idx in range(start, NFIELDS):
                if a[i].fields[idx] != b[i].fields[idx]:
                    out.append("  field %-8s left=%r right=%r" %
                               (names[idx], a[i].fields[idx], b[i].fields[idx]))
            lo = max(0, i - context)
            hi = min(n, i + context + 1)
            out.append("  context (left | right):")
            for j in range(lo, hi):
                la = a[j].raw if j < len(a) else "<missing>"
                lb = b[j].raw if j < len(b) else "<missing>"
                out.append("  %s %4d | %s" % ("=>" if j == i else "  ", j + 1, la))
                out.append("  %s %4d | %s" % ("=>" if j == i else "  ", j + 1, lb))
            return False, "\n".join(out)
    return True, "traces are identical over %d retirement records%s" % (
        len(a), " (retire cycles ignored)" if ignore_cycles else "")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="diff two MiniCPU-16 retirement traces")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--ignore-cycles", action="store_true",
                   help="ignore the retire_cycle column (architectural comparison)")
    p.add_argument("--context", type=int, default=3)
    ns = p.parse_args(argv)
    try:
        a = parse_trace(ns.left)
        b = parse_trace(ns.right)
    except (OSError, TraceFormatError) as e:
        print("trace_diff: %s" % e)
        return 2
    ok, msg = diff_traces(a, b, ns.ignore_cycles, ns.context)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
