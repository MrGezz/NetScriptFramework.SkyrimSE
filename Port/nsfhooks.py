r"""Check — and where possible re-derive — every byte-pattern-guarded hook site of the
SkyrimSE layer and its plugins against a new executable.

    python nsfhooks.py --exe <SkyrimSE.exe> --report <fndiff report.tsv> --cache <fndiff cache>
                       --old-exe <old exe> --old-al <old versionlib bin>
                       [--events <Events.h>] [--plugins <...\SkyrimSE\Plugins>]
                       [--out <patch.tsv>]

Sites are read from
  Events.h   EventHookParameters<T>(__VIDS::VIDn.Value[+off], include, replace, "pattern", ...)
  plugins    Memory.(Try)GetAddressOf(n, extra, patternOffset, "pattern")

A site is `VID + offset` plus the bytes expected there. Function *starts* carry across builds
(fndiff resolves them), but Anniversary Edition recompiled the bodies, so an interior offset
almost never survives. For every site this tool therefore:

  1. translates the VID through the fndiff report,
  2. checks the pattern at `new(VID) + offset`,
  3. if that fails, searches the *containing new function* for the pattern and reports the
     candidate offsets — a unique candidate is a proposed new offset, several candidates or
     none is hand work,
  4. checks the same pattern at the old address, which tells a wrong translation apart from
     a hook site the compiler moved.

Nothing is rewritten in the sources: the output is a TSV of proposals for a human to apply.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "AddressLibraryManager", "DiffCalculator"))

import addrlib   # noqa: E402
import fndiff    # noqa: E402

_EVENT = re.compile(
    r"EventHookParameters<(\w+)\^>\(__VIDS::VID(\d+)\.Value\s*(?:([+-])\s*(0x[0-9A-Fa-f]+|\d+))?,"
    r"\s*(\d+),\s*(\d+),\s*\"([0-9A-Fa-f? ]*)\"")
_PLUGIN = re.compile(
    r"(?:Try)?GetAddressOf\(\s*(\d+)\s*,\s*(-?0x[0-9A-Fa-f]+|-?\d+)\s*,\s*(-?0x[0-9A-Fa-f]+|-?\d+)"
    r"\s*,\s*\"([0-9A-Fa-f? ]+)\"")


def parse_pattern(s):
    """"48 8D 0D" / "E8" / "?? ?? 90" -> [byte or None]; a lone `?` is a wildcard too."""
    out = []
    for tok in s.split():
        if "?" in tok:
            out.append(None)
        else:
            out.append(int(tok, 16))
    return out


def matches(data, pat, at):
    if at + len(pat) > len(data):
        return False
    return all(p is None or p == data[at + i] for i, p in enumerate(pat))


def find_all(data, pat, limit=64):
    out = []
    for i in range(0, len(data) - len(pat) + 1):
        if matches(data, pat, i):
            out.append(i)
            if len(out) >= limit:
                break
    return out


def read_sites(events, plugins):
    sites = []
    if events and os.path.isfile(events):
        with open(events, encoding="utf-8", errors="replace") as f:
            for m in _EVENT.finditer(f.read()):
                off = int(m.group(4), 0) if m.group(4) else 0
                if m.group(3) == "-":
                    off = -off
                sites.append(("Events.h", m.group(1), int(m.group(2)), off, m.group(7)))
    if plugins and os.path.isdir(plugins):
        for root, _dirs, files in os.walk(plugins):
            for name in files:
                if not name.endswith(".cs"):
                    continue
                plugin = os.path.relpath(root, plugins).split(os.sep)[0]
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                    for m in _PLUGIN.finditer(f.read()):
                        sites.append((plugin, name, int(m.group(1)),
                                      int(m.group(2), 0) + int(m.group(3), 0), m.group(4)))
    return sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--cache")
    ap.add_argument("--old-exe")
    ap.add_argument("--old-al")
    ap.add_argument("--events", default=os.path.join(HERE, "..", "NetScriptFramework.SkyrimSE", "Events.h"))
    ap.add_argument("--plugins", default=os.path.join(HERE, "..", "..", "NetScriptFramework.Plugins", "SkyrimSE", "Plugins"))
    ap.add_argument("--out")
    args = ap.parse_args()

    new = fndiff.Image(args.exe, cache=args.cache)
    old = fndiff.Image(args.old_exe, cache=args.cache) if args.old_exe else None
    old_al = addrlib.read_bin(args.old_al).values if args.old_al else {}

    trans, method = {}, {}
    with open(args.report, encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4 and p[2]:
                trans[int(p[0])] = int(p[2], 16)
                method[int(p[0])] = p[3]

    sites = read_sites(args.events, args.plugins)
    rows = []
    tally = collections.Counter()
    per_src = collections.defaultdict(collections.Counter)

    for src, where, vid, off, pattern in sites:
        pat = parse_pattern(pattern)
        old_state = ""
        if old is not None and vid in old_al:
            oa = old_al[vid] + off
            od = old.pe.read(oa, len(pat))
            old_state = "ok" if matches(od, pat, 0) else "MISMATCH"
        if vid not in trans:
            verdict, proposal, note = "unresolved-vid", "", "fndiff could not carry this id"
        else:
            base = trans[vid]
            data = new.pe.read(base + off, len(pat))
            if matches(data, pat, 0):
                verdict, proposal, note = "ok", hex(off), ""
            else:
                fn = new.locate(base)
                if fn is None:
                    verdict, proposal, note = "no-function", "", "translated address is not inside a function"
                else:
                    body = new.pe.read(fn.begin, fn.end - fn.begin)
                    hits = find_all(body, pat)
                    rel = [fn.begin - base + h for h in hits]
                    if len(rel) == 1:
                        verdict, proposal, note = "relocate", hex(rel[0]), "unique match in the new function"
                    elif rel:
                        verdict, proposal = "ambiguous", ",".join(hex(r) for r in rel[:8])
                        note = "%d matches in the new function" % len(rel)
                    else:
                        verdict, proposal, note = "gone", "", "pattern absent from the new function"
        tally[verdict] += 1
        per_src[src][verdict] += 1
        rows.append((src, where, vid, hex(off), pattern, method.get(vid, ""), verdict, proposal, old_state, note))

    w = max(len(r[0]) for r in rows) if rows else 10
    for r in rows:
        if r[6] == "ok":
            continue
        print("%-*s %-34s VID %-7d %-8s %-11s %-9s %-24s old:%-8s %s" % (
            w, r[0], r[1][:34], r[2], r[3], r[5], r[6], r[7], r[8], r[9]))
    print()
    print("%d hook sites: %s" % (len(rows), ", ".join("%s %d" % (k, v) for k, v in tally.most_common())))
    for src in sorted(per_src):
        print("  %-*s %s" % (w, src, ", ".join("%s %d" % (k, v) for k, v in per_src[src].most_common())))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("source\twhere\tvid\told_offset\tpattern\tvid_method\tverdict\tproposed_offset\told_image\tnote\n")
            for r in rows:
                f.write("\t".join(str(x) for x in r) + "\n")
        print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
