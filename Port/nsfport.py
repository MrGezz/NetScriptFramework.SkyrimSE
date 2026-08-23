"""Generate the .NET Script Framework version library for a new Skyrim SE executable.

    python nsfport.py --exe <SkyrimSE.exe> --old-exe <SkyrimSE 1.5.97 unpacked>
                      --report <fndiff report.tsv> --cache <fndiff cache dir>
                      --al <versionlib-<new>.bin> --rename <skyrimae.rename>
                      --sizes <sizes.tsv> --out <NetScriptFramework.SkyrimSE.<ver>.bin>
                      [--impl <Implementations.cs>] [--gt <ground truth tsv>]
                      [--lib-version 14] [--hash 0x14AEB93E09DDA87F] [--report-out <txt>]

What goes into the bin (see nsfbin.py for the format) and where it comes from:

  functions     every function of the new image (begin/end from the fndiff image inventory);
                the ones an old Address Library ID was carried to by fndiff keep that ID as
                their VID, so Vids.h and the plugins resolve unchanged. Names come from the
                Address Library Manager's .rename list through the new library's own IDs.
  globals       data IDs carried by fndiff.
  types         one per type VID the generated bindings register (Implementations.cs
                NetScriptFramework_SkyrimSE_TypeRegistrations); vtable from the new image's
                RTTI, matched by demangled class name (ambiguities settled against the old
                image's RTTI base-class layout, which is what the bindings were generated
                from); size from CommonLibSSE's static_asserts when known.
  registrations (interface id, implementation id, vtable, offset-in-object) for every vtable
                of every matched class, and a vtable-less entry for the rest.
  type instances the base-class sub-objects of each implementation from the new RTTI.
  cached values the CVT table below: member offsets the bindings read from the library
                instead of hard-coding (they were generated from IDA for 1.5.97; the 1.7.99
                values are taken from CommonLibSSE's headers).
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "AddressLibraryManager", "DiffCalculator"))

import addrlib      # noqa: E402
import fndiff       # noqa: E402
import msvcrtti     # noqa: E402
import nsfbin       # noqa: E402
import relib        # noqa: E402

# ----------------------------------------------------------------------------------
# Cached values for Skyrim SE 1.7.99. Index -> member offset (None = not derivable).
# Which member each index stands for was read off Implementations.cs (the property
# whose getter adds __CVTS.CVTn.Value to this.Address); the offsets are CommonLibSSE
# (po3, 2026-08-20, 1.7.99 headers) unless noted.
# ----------------------------------------------------------------------------------
CACHED_VALUES_1_7_99 = {
    # BSAnimationGraphManager (AE: BSAnimationGraphVariableCache grew 0x28 -> 0x30)
    2: 0x58,    # subManagers
    3: 0x70,    # variableCache
    4: 0xA0,    # updateLock
    5: 0xA8,    # dependentManagerLock
    6: 0xB0,    # activeGraph
    7: 0xB4,    # generateDepth
    # NiControllerSequence members, read through BSAnimGroupSequence (8..26) and
    # NiControllerSequence itself (252..270); unchanged since 1.5.97
    8: 0x28, 9: 0x30, 10: 0x38, 11: 0x40, 12: 0x44, 13: 0x48, 14: 0x4C, 15: 0x50, 16: 0x54,
    17: 0x58, 18: 0x60, 19: 0x68, 20: 0x6C, 21: 0x70, 22: 0x74, 23: 0x78, 24: 0x80, 25: 0x88, 26: 0x90,
    252: 0x28, 253: 0x30, 254: 0x38, 255: 0x40, 256: 0x44, 257: 0x48, 258: 0x4C, 259: 0x50, 260: 0x54,
    261: 0x58, 262: 0x60, 263: 0x68, 264: 0x6C, 265: 0x70, 266: 0x74, 267: 0x78, 268: 0x80, 269: 0x88, 270: 0x90,
    # BSCullingProcess.cullMode / compoundFrustum / recurseToGeometry, through
    # BSCullingProcess (43/44/51), BSGeometryListCullingProcess (63/64/71) and
    # BSParabolicCullingProcess (83/84/91)
    43: 0x30198, 44: 0x301A0, 51: 0x301D4,
    63: 0x30198, 64: 0x301A0, 71: 0x301D4,
    83: 0x30198, 84: 0x301A0, 91: 0x301D4,
    # NiTMapItem<Key, T>::second: next 0x00, first 0x08, second after first (aligned)
    96: 0x0C,   # <ObjectRefHandle, ObjectRefHandle>
    97: 0x10,   # <ObjectRefHandle, NiNode*>
    98: 0x10,   # <ObjectRefHandle, NiPointer<BSMultiBoundNode>>
    99: 0x0C,   # <ObjectRefHandle, uint32>
    100: 0x10,  # <ENUM_FORM_ID, BSSimpleList<SavedFormData>*>
    101: 0x09,  # <uchar, bool>
    # FxResponseArgs<0>::_index = 0x08 + (SIZE + 1) * sizeof(GFxValue 0x18)
    102: 0x20,
    # Main
    227: 0x10,  # quitGame
    233: 0x16,  # freezeTime ("PauseGameWorld", the tfc 1 flag)
    235: 0x18,  # wnd
    236: 0x20,  # instance
    237: 0x28,  # threadID
    240: 0x38,  # packedTaskHeap
    241: 0xC8,  # taskQueue
    242: 0x108, # secondaryPackedTaskHeap
    243: 0x198, # secondaryTaskQueue
    248: 0x1E0, # saveDataBackgroundImages
    249: 0x228, # saveDataIconImages
    251: 0x08,  # BSTEventSink<BSGamerProfileEvent> sub-object (Cast<T>)
    # BSTHashMapEntry<unknown, unknown> (291/292), BSTFreeListElem<unknown> (293):
    # the element types are unknown to the bindings too; left unset on purpose
    # BSTHashMapEntry<ObjectRefHandle, BSCurrentAction*>: value 0x08, next 0x10
    294: 0x08, 295: 0x10,
    # TESClimate::Timing: sunset 0x02, volatility 0x04, moonPhaseLength 0x05
    296: 0x02, 297: 0x04, 298: 0x05,
    # BSTHashMapEntry<BSFixedStringW, BSFixedStringW>: value 0x08, next 0x10
    303: 0x08, 304: 0x10,
    # BSTFreeListElem<T>::next = sizeof(T): ActorMovementMessageMap<16>::RawMessageHandlerWrapperType unknown (316),
    # BSScript::Internal::FunctionMessage 0x18 (317)
    317: 0x18,
    # NiTMapItem<ActorHandle, WadingWaterData>::second: WadingWaterData layout unknown (322)
}
CACHED_VALUE_COUNT = 325   # __CVTS::_Init reads indices 0..324

# C# spellings the generated interfaces use for basic template arguments -> C++
_CS_TYPES = {
    "System.Byte": "char", "System.SByte": "char", "System.Boolean": "bool",
    "System.Int16": "short", "System.UInt16": "short", "System.Int32": "int", "System.UInt32": "int",
    "System.Int64": "__int64", "System.UInt64": "__int64", "System.Single": "float", "System.Double": "double",
    "System.Char": "wchar_t", "System.IntPtr": "#", "System.UIntPtr": "#", "unknown": "#",
}


def canonical_nsf(name: str) -> str:
    """The bindings' spelling of a class name, brought into msvcrtti.canonical's form:
    `.` is the namespace separator, generic arguments the generator could not name are
    `UnknownGenArg_N`, and pointers/literal template arguments are not expressed at all."""
    s = name
    for k, v in _CS_TYPES.items():
        s = s.replace(k, v)
    s = re.sub(r"UnknownGenArg_\d+", "#", s)
    s = s.replace(" ", "").replace(".", "::")
    s = re.sub(r"#+", "#", s)
    return s


_TUPLE = re.compile(r"Tuple<System\.Int32, System\.Type, System\.UInt64>\((-?\d+), typeof\((.+?)\), (\d+)\)\);")
_CLASS = re.compile(r"^\tinternal sealed class (impl_[0-9a-f]+_\S+) : NetScriptFramework\.(VirtualObject|MemoryObject), (.+)$")
_CAST = re.compile(r"^\t\t\tif \(t == typeof\((.+)\)\) offset = (0x[0-9A-Fa-f]+|\d+|__CVTS\.CVT\d+\.ValueSafe);$")


def read_bindings(path):
    """Implementations.cs -> (impls, ifaces) where
    impls:  impl id -> {"type": impl class, "vid": type VID, "virtual": bool, "iface": interface name, "casts": {name: offset}}
    ifaces: interface name -> interface id (positive)"""
    impls = {}
    ifaces = {}
    by_impl_class = {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for a, t, v in _TUPLE.findall(text):
        a, v = int(a), int(v)
        if a > 0:
            impls[a] = {"type": t, "vid": v, "virtual": None, "iface": None, "casts": {}}
            by_impl_class[t] = a
        else:
            ifaces[t] = -a
    by_prefix = collections.defaultdict(list)
    for name in ifaces:
        by_prefix[name.split("<", 1)[0].split(",", 1)[0]].append(name)
    cur = None
    for line in text.split("\n"):
        m = _CLASS.match(line.rstrip("\r"))
        if m:
            cur = by_impl_class.get(m.group(1))
            if cur is None:
                continue
            impls[cur]["virtual"] = m.group(2) == "VirtualObject"
            # the interface list can contain generics with ", " inside, so take the longest
            # registered interface name the list starts with rather than splitting on ", "
            decl = m.group(3)
            best = None
            for name in by_prefix.get(decl.split("<", 1)[0].split(",", 1)[0], ()):
                if decl.startswith(name) and (best is None or len(name) > len(best)):
                    best = name
            impls[cur]["iface"] = best
            continue
        if cur is None:
            continue
        m = _CAST.match(line.rstrip("\r"))
        if m and not m.group(2).startswith("__CVTS"):
            impls[cur]["casts"][m.group(1)] = int(m.group(2), 0)
    return impls, ifaces


def class_bases(rt, mangled):
    """{(canonical base name, offset)} of a class in an image (non-virtual bases only)."""
    c = rt.classes.get(mangled)
    if not c:
        return set()
    return {(msvcrtti.canonical(msvcrtti.demangle(n)), o) for n, o, virt in c["bases"] if not virt}


def match_types(impls, rt_new, rt_old):
    """Match every virtual implementation of the bindings to a class of the new image by
    canonical name; when several classes share a name modulo literal template arguments,
    pick the one whose base-class layout in the OLD image is what the bindings' Cast<T>
    offsets were generated from.  Returns (impl id -> mangled, [(impl id, name, reason)],
    number resolved by layout)."""
    canon_new = collections.defaultdict(list)
    loose_new = collections.defaultdict(list)
    for m in rt_new.classes:
        c = msvcrtti.canonical(msvcrtti.demangle(m))
        canon_new[c].append(m)
        loose_new[msvcrtti.loose(c)].append(m)
    matched = {}
    unmatched = []
    ambiguous_resolved = 0
    for a, d in impls.items():
        if not d["virtual"] or d["iface"] is None:
            continue
        key = canonical_nsf(d["iface"])
        cands = canon_new.get(key)
        if not cands:
            # the bindings cannot spell pointers or literal template arguments; retry
            # without them
            cands = loose_new.get(msvcrtti.loose(key))
        if not cands:
            unmatched.append((a, d["iface"], "no class with this name in the new image"))
            continue
        if len(cands) == 1:
            matched[a] = cands[0]
            continue
        want = {(canonical_nsf(n), o) for n, o in d["casts"].items()}
        best, best_score = None, None
        for m in cands:
            ob = class_bases(rt_old, m)
            score = len(want & ob) - len(want ^ ob) * 0.01
            if best_score is None or score > best_score:
                best, best_score = m, score
        matched[a] = best
        ambiguous_resolved += 1
    seen = {}
    for a in sorted(matched):
        m = matched[a]
        if m in seen:
            unmatched.append((a, impls[a]["iface"], "same class as implementation %d" % seen[m]))
            del matched[a]
        else:
            seen[m] = a
    return matched, unmatched, ambiguous_resolved


def read_report(path):
    """fndiff report.tsv -> {old id: (old offset, new offset or None, method, section)}"""
    out = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            vid = int(p[0])
            old = int(p[1], 16)
            new = int(p[2], 16) if p[2] else None
            out[vid] = (old, new, p[3], p[4])
    return out


def read_sizes(path):
    sizes = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) != 2:
                continue
            name = msvcrtti.canonical(p[0].replace("RE::", ""))
            v = int(p[1], 0)
            sizes[name] = max(sizes.get(name, 0), v)   # AE never shrank a class that is asserted twice
    return sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--old-exe", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--al", required=True, help="official Address Library bin of the new version (for names)")
    ap.add_argument("--rename", required=True)
    ap.add_argument("--sizes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--impl", default=os.path.join(HERE, "..", "NetScriptFramework.SkyrimSE.Implementations", "Implementations.cs"))
    ap.add_argument("--gt")
    ap.add_argument("--lib-version", type=int, default=14)
    ap.add_argument("--hash", default="0x14AEB93E09DDA87F")
    ap.add_argument("--report-out")
    ap.add_argument("--vids", help="text file with one VID per line to check for resolution")
    args = ap.parse_args()

    log_lines = []

    def log(msg=""):
        print(msg)
        log_lines.append(msg)

    # ---------------------------------------------------------------- inputs
    new_img = fndiff.Image(args.exe, cache=args.cache)
    file_version = tuple(new_img.pe.file_version() or (0, 0, 0, 0))
    rt_new = msvcrtti.Rtti(args.exe)
    rt_old = msvcrtti.Rtti(args.old_exe)
    report = read_report(args.report)
    impls, ifaces = read_bindings(args.impl)
    sizes = read_sizes(args.sizes)
    al = addrlib.read_bin(args.al)
    names_by_id = relib.read_rename(args.rename)
    name_by_off = {}
    for aid, off in al.values.items():
        n = names_by_id.get(aid)
        if n:
            name_by_off[off] = n
    log("new image: %d functions, RTTI %d classes / %d vtables; old image RTTI %d classes" % (
        len(new_img.fns), len(rt_new.classes), len(rt_new.vtables), len(rt_old.classes)))
    log("bindings: %d implementations, %d interfaces; report: %d ids, %d resolved" % (
        len(impls), len(ifaces), len(report), sum(1 for v in report.values() if v[1] is not None)))

    # ---------------------------------------------------------------- ground truth
    if args.gt:
        by_old = {v[0]: v for v in report.values()}
        ok = wrong = unres = missing = 0
        wrong_methods = collections.Counter()
        with open(args.gt, encoding="utf-8") as f:
            next(f)
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 5:
                    continue
                o, n = int(p[3], 16), int(p[4], 16)
                r = by_old.get(o)
                if r is None:
                    missing += 1
                elif r[1] is None:
                    unres += 1
                elif r[1] == n:
                    ok += 1
                else:
                    wrong += 1
                    wrong_methods[r[2]] += 1
        log("ground truth: %d correct, %d wrong, %d unresolved, %d not in report (%.2f%% of resolved correct); wrong by method: %s" % (
            ok, wrong, unres, missing, 100.0 * ok / max(1, ok + wrong), dict(wrong_methods)))

    # ---------------------------------------------------------------- type matching
    matched, unmatched, ambiguous_resolved = match_types(impls, rt_new, rt_old)
    n_virtual = sum(1 for d in impls.values() if d["virtual"])
    log("types: %d virtual implementations, %d matched to a class (%d by base-class layout), %d unmatched" % (
        n_virtual, len(matched), ambiguous_resolved, len(unmatched)))

    # Cast<T> sanity: offsets of matched classes' bases in the old image vs the bindings
    cast_ok = cast_bad = 0
    for a, m in matched.items():
        ob = class_bases(rt_old, m)
        for n, o in impls[a]["casts"].items():
            if (canonical_nsf(n), o) in ob:
                cast_ok += 1
            elif any(cn == canonical_nsf(n) for cn, _ in ob):
                cast_bad += 1
    log("old-image base offsets agree with the bindings' Cast<T> for %d of %d checkable entries" % (cast_ok, cast_ok + cast_bad))

    # ---------------------------------------------------------------- build the library
    lib = nsfbin.Library()
    lib.library_version = args.lib_version
    # Main.LoadGameInfo builds the file name from Memory.GetMainModuleVersion(), so the
    # version inside the library must be the executable's own file version.
    lib.file_version = file_version
    expect = "NetScriptFramework.SkyrimSE.%s.bin" % "_".join(str(x) for x in file_version)
    if os.path.basename(args.out) != expect:
        log("NOTE: the framework will look for %s, not %s" % (expect, os.path.basename(args.out)))
    lib.library_base_offset = new_img.pe.image_base
    lib.hash_version = int(args.hash, 0)

    vid_by_mangled = {}
    type_by_vid = {}
    for a, d in impls.items():
        v = d["vid"]
        if v == 0 or v in type_by_vid:
            continue
        m = matched.get(a)
        vt = 0
        if m is not None:
            prim = [rva for rva, off in rt_new.classes[m]["vtables"] if off == 0]
            vt = prim[0] if prim else rt_new.classes[m]["vtables"][0][0]
            vid_by_mangled[m] = v
        size = sizes.get(canonical_nsf(d["iface"] or "")) if d["iface"] else None
        t = nsfbin.TypeInfo(v, vt, d["iface"] or d["type"], size, None)
        type_by_vid[v] = t
        lib.types.append(t)
    log("types written: %d (%d with a vtable, %d with a size)" % (
        len(lib.types), sum(1 for t in lib.types if t.vtable), sum(1 for t in lib.types if t.size is not None)))

    # registrations
    for a, d in impls.items():
        if d["iface"] is None or d["iface"] not in ifaces:
            continue
        iid = ifaces[d["iface"]]
        m = matched.get(a)
        if m is not None:
            for rva, off in rt_new.classes[m]["vtables"]:
                lib.registrations.append(nsfbin.Registration(iid, a, rva, off))
        else:
            lib.registrations.append(nsfbin.Registration(iid, a, -1, 0))
    # type instances: the sub-objects of each implementation in the new layout
    for a, m in matched.items():
        c = rt_new.classes[m]
        ls = []
        for n, o, virt in c["bases"]:
            if virt:
                continue
            v = vid_by_mangled.get(n)
            if v is None:
                continue
            t = type_by_vid.get(v)
            end = o + t.size if (t and t.size is not None) else None
            ls.append(nsfbin.TypeInstance(o, end, v))
        if ls:
            lib.type_instances[a] = ls
    log("registrations: %d (%d with a vtable); type-instance lists: %d" % (
        len(lib.registrations), sum(1 for r in lib.registrations if r.vtable_offset >= 0), len(lib.type_instances)))

    # functions and globals
    fn_vid = {}
    globals_ = []
    inside = 0
    for vid, (_old, new, _method, section) in report.items():
        if new is None:
            continue
        if section == ".text" or new_img.is_code(new):
            f = new_img.locate(new)
            if f is not None and f.begin == new:
                if new in fn_vid:
                    continue
                fn_vid[new] = vid
            else:
                inside += 1
                globals_.append(nsfbin.GlobalInfo(vid, new, name_by_off.get(new), "code"))
        else:
            globals_.append(nsfbin.GlobalInfo(vid, new, name_by_off.get(new), None))
    for f in new_img.fns:
        lib.functions.append(nsfbin.FunctionInfo(fn_vid.get(f.begin, 0), f.begin, f.end, name_by_off.get(f.begin), None))
    lib.functions.sort(key=lambda x: x.begin)
    lib.globals = globals_
    log("functions: %d (%d carry a VID, %d named); globals: %d (%d are code addresses inside functions)" % (
        len(lib.functions), len(fn_vid), sum(1 for x in lib.functions if x.short_name), len(lib.globals), inside))

    # cached values
    lib.cached_values = [CACHED_VALUES_1_7_99.get(i) for i in range(CACHED_VALUE_COUNT)]

    nsfbin.write(args.out, lib)
    log("wrote %s (%d bytes)" % (args.out, os.path.getsize(args.out)))

    # ---------------------------------------------------------------- VID check
    if args.vids:
        want = [int(x) for x in open(args.vids) if x.strip()]
        have = {x.id for x in lib.functions if x.id} | {g.id for g in lib.globals}
        missing = [v for v in want if v not in have]
        methods = collections.Counter(report[v][2] for v in want if v in report and report[v][1] is not None)
        log("VIDs required by the SkyrimSE layer and the plugins: %d, resolved %d, missing %d: %s" % (
            len(want), len(want) - len(missing), len(missing), missing))
        log("  resolution methods: %s" % dict(methods))

    if unmatched:
        log("unmatched virtual implementations (vtable-less registration written):")
        for a, name, why in unmatched[:60]:
            log("  %5d %-80s %s" % (a, name[:80], why))
        if len(unmatched) > 60:
            log("  ... %d more" % (len(unmatched) - 60))

    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
            if unmatched:
                f.write("\nall unmatched:\n")
                for a, name, why in unmatched:
                    f.write("%5d\t%s\t%s\n" % (a, name, why))


if __name__ == "__main__":
    main()
