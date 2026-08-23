"""MSVC RTTI reader for a 64-bit PE image (vtables, Complete Object Locators, class
hierarchies) plus demangling of the `.?AV...` type names through dbghelp.

    rtti = Rtti(r"SkyrimSE.exe")
    rtti.classes[b".?AVActor@@"]  ->  {"vtables": [(rva, offset_in_object)], "bases": [(mangled, mdisp)]}

Needs pe.py from AddressLibraryManager\\DiffCalculator on sys.path.
"""
import ctypes
import re
import struct

import pe

_ANON = re.compile(rb"\?A0x[0-9a-f]{8}")
_LAMBDA = re.compile(rb"lambda_[0-9a-f]+")


def norm_mangled(raw: bytes) -> bytes:
    """Anonymous-namespace hashes and lambda numbers differ per build; neutralise them."""
    return _ANON.sub(b"?A0x", _LAMBDA.sub(b"lambda_", raw))


class Rtti:
    def __init__(self, path):
        self.pe = pe.PE(path)
        self.ptr = self.pe.pointer_targets()
        self.vtables = {}     # vtable rva -> (mangled, offset)
        self.classes = {}     # mangled -> {"vtables": [...], "bases": [...], "td": rva}
        self._scan()

    def _td_name(self, td):
        raw = self.pe.read(td + 16, 256).split(b"\0", 1)[0]
        if not raw.startswith(b".?A"):
            return None
        return norm_mangled(raw)

    def _scan(self):
        chd_done = {}
        for slot, target in self.ptr.items():
            d = self.pe.read(target, 24)
            if len(d) < 24:
                continue
            sig, off, _cd, td, chd, self_rva = struct.unpack("<6I", d)
            if sig != 1 or self_rva != target:
                continue
            name = self._td_name(td)
            if name is None:
                continue
            vt = slot + 8
            self.vtables[vt] = (name, off)
            c = self.classes.setdefault(name, {"vtables": [], "bases": None, "td": td})
            c["vtables"].append((vt, off))
            if c["bases"] is None:
                c["bases"] = chd_done.get(chd)
                if c["bases"] is None:
                    c["bases"] = chd_done[chd] = self._bases(chd)
        for c in self.classes.values():
            c["vtables"].sort(key=lambda t: t[1])

    def _bases(self, chd):
        h = self.pe.read(chd, 16)
        if len(h) != 16:
            return []
        _sig, _attr, nb, bca = struct.unpack("<4I", h)
        out = []
        for i in range(min(nb, 256)):
            b = self.pe.read(bca + 4 * i, 4)
            if len(b) != 4:
                break
            bcd = struct.unpack("<I", b)[0]
            d = self.pe.read(bcd, 28)
            if len(d) < 28:
                break
            td, _ncb, mdisp, pdisp, vdisp, _attr2, _chd2 = struct.unpack("<IIiiiII", d)
            name = self._td_name(td)
            if name is None:
                continue
            if pdisp != -1:
                # virtual base: the offset lives in the vbtable at runtime; keep the
                # mdisp part only and flag it
                out.append((name, mdisp, True))
            else:
                out.append((name, mdisp, False))
        return out


# ---------------------------------------------------------------- demangling

_dbghelp = None
_demangled = {}


def demangle(mangled: bytes) -> str:
    """Demangle an RTTI type name (`.?AVFoo@@`) via dbghelp!UnDecorateSymbolName, by
    dressing it up as the type descriptor symbol `??_R0?AVFoo@@@8`."""
    global _dbghelp
    s = _demangled.get(mangled)
    if s is not None:
        return s
    if _dbghelp is None:
        _dbghelp = ctypes.WinDLL("dbghelp")
        _dbghelp.UnDecorateSymbolName.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
        _dbghelp.UnDecorateSymbolName.restype = ctypes.c_uint
    body = mangled[1:] if mangled.startswith(b".") else mangled
    sym = b"??_R0" + body + b"@8"
    buf = ctypes.create_string_buffer(8192)
    n = _dbghelp.UnDecorateSymbolName(sym, buf, 8192, 0)
    s = buf.value.decode("latin1") if n else sym.decode("latin1")
    if s.startswith("??_R0"):
        s = mangled.decode("latin1")          # undecoration failed, keep the raw name
    s = s.replace(" `RTTI Type Descriptor'", "")
    _demangled[mangled] = s
    return s


_WORDS = re.compile(r"\b(class|struct|union|enum|const|volatile|__ptr64|__cdecl|__fastcall|__stdcall|__thiscall|unsigned|signed)\b")
_INT = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?![A-Za-z0-9_])")


def canonical(demangled: str) -> str:
    """Compact comparable form of a demangled class name: no spaces, no elaborated-type
    keywords, integer template arguments and pointer/reference markers replaced by `#`.
    The name structure is preserved — only tokens the C# side cannot express are erased."""
    s = demangled.replace("`anonymous namespace'", "anonymous_namespace")
    s = s.replace("`anonymous-namespace'", "anonymous_namespace")
    s = _WORDS.sub("", s)
    s = s.replace(" ", "")
    s = re.sub(r"[*&]+", "#", s)          # pointer / reference markers, in place
    s = _INT.sub("#", s)                  # literal template arguments
    s = re.sub(r"#+", "#", s)
    return s


def loose(canonical_name: str) -> str:
    """Weaker key for matching against a source that cannot express pointers or literal
    template arguments at all: drops every `#` and any now-empty argument slots."""
    s = canonical_name.replace("#", "")
    s = re.sub(r",+", ",", s)
    s = s.replace("<,", "<").replace(",>", ">").replace("<>", "")
    return s
