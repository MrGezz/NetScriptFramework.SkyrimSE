"""Reader / writer for the .NET Script Framework version library
(Data\\NetScriptFramework\\NetScriptFramework.<Game>.<ver>.bin).

The layout is transcribed from NetScriptFramework\\Framework\\GameInfo.cs
(ReadFromStream / WriteToStream, StreamVersion 2): a GZip stream holding a
.NET BinaryWriter record sequence. Strings are BinaryWriter strings
(7-bit length prefix, UTF-8). All addresses are offsets from the module
base (RVAs), the same space as the SKSE Address Library.
"""
import gzip
import struct
from dataclasses import dataclass, field
from typing import Optional

STREAM_VERSION = 2


@dataclass
class Field:
    field_id: int
    begin: Optional[int]
    short_name: Optional[str]
    type_name: Optional[str]


@dataclass
class TypeInfo:
    id: int
    vtable: int            # RVA or 0
    name: str
    size: Optional[int]
    fields: Optional[list] = None


@dataclass
class FunctionInfo:
    id: int                # VID (0 = anonymous)
    begin: int
    end: int
    short_name: Optional[str]
    full_name: Optional[str]


@dataclass
class GlobalInfo:
    id: int
    begin: int
    short_name: Optional[str]
    type_name: Optional[str]


@dataclass
class Registration:
    interface_id: int
    implementation_id: int
    vtable_offset: int     # RVA or -1
    offset_in_type: int


@dataclass
class TypeInstance:
    begin: Optional[int]
    end: Optional[int]
    type_id: int


@dataclass
class Library:
    library_version: int = 0
    file_version: tuple = (0, 0, 0, 0)
    alias: Optional[tuple] = None
    library_base_offset: int = 0
    hash_version: int = 0
    types: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    globals: list = field(default_factory=list)
    registrations: list = field(default_factory=list)
    type_instances: dict = field(default_factory=dict)   # impl id -> [TypeInstance]
    cached_values: list = field(default_factory=list)    # int or None


class _R:
    def __init__(self, data: bytes):
        self.b = data
        self.p = 0

    def _u(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.p)[0]
        self.p += struct.calcsize(fmt)
        return v

    def u8(self): return self._u("<B")
    def u16(self): return self._u("<H")
    def i32(self): return self._u("<i")
    def u32(self): return self._u("<I")
    def u64(self): return self._u("<Q")

    def string(self):
        n = 0
        shift = 0
        while True:
            c = self.u8()
            n |= (c & 0x7F) << shift
            if not (c & 0x80):
                break
            shift += 7
        s = self.b[self.p:self.p + n].decode("utf-8")
        self.p += n
        return s

    def eof(self):
        return self.p >= len(self.b)


class _W:
    def __init__(self):
        self.out = bytearray()

    def _w(self, fmt, v):
        self.out += struct.pack(fmt, v)

    def u8(self, v): self._w("<B", v)
    def u16(self, v): self._w("<H", v)
    def i32(self, v): self._w("<i", v)
    def u32(self, v): self._w("<I", v)
    def u64(self, v): self._w("<Q", v)

    def string(self, s):
        data = s.encode("utf-8")
        n = len(data)
        while True:
            c = n & 0x7F
            n >>= 7
            if n:
                self.u8(c | 0x80)
            else:
                self.u8(c)
                break
        self.out += data


def _read_type(r: _R) -> TypeInfo:
    lt = r.u8()
    vtable = 0
    if lt & 1:
        vtable = r.u64() if lt & 2 else r.u32()
    name = r.string()
    size = r.i32() if lt & 4 else None
    tid = r.u64() if lt & 8 else r.u32()
    fields = None
    if lt & 0x10:
        cn = r.i32()
        fields = []
        for _ in range(cn):
            ft = r.u8()
            begin = None
            if ft & 1:
                begin = r.i32() if ft & 2 else r.u16()
            sn = r.string() if ft & 4 else None
            tn = r.string() if ft & 8 else None
            if ft & 0x10:
                fid = r.u8() if ft & 0x20 else r.u16()
            else:
                fid = r.u32()
            fields.append(Field(fid, begin, sn, tn))
    return TypeInfo(tid, vtable, name, size, fields)


def _write_type(w: _W, t: TypeInfo):
    lt = 0
    if t.vtable:
        lt |= 1
        if t.vtable > 0xFFFFFFFF:
            lt |= 2
    if t.size is not None:
        lt |= 4
    if t.id > 0xFFFFFFFF:
        lt |= 8
    if t.fields:
        lt |= 0x10
    w.u8(lt)
    if lt & 1:
        (w.u64 if lt & 2 else w.u32)(t.vtable)
    w.string(t.name)
    if lt & 4:
        w.i32(t.size)
    (w.u64 if lt & 8 else w.u32)(t.id)
    if lt & 0x10:
        fields = t.fields or []
        w.i32(len(fields))
        for f in fields:
            ft = 0
            if f.begin is not None:
                ft |= 1
                if f.begin < 0 or f.begin > 0xFFFF:
                    ft |= 2
            if f.short_name:
                ft |= 4
            if f.type_name:
                ft |= 8
            if f.field_id <= 0xFF:
                ft |= 0x30
            elif f.field_id <= 0xFFFF:
                ft |= 0x10
            w.u8(ft)
            if ft & 1:
                (w.i32 if ft & 2 else w.u16)(f.begin)
            if ft & 4:
                w.string(f.short_name)
            if ft & 8:
                w.string(f.type_name)
            if ft & 0x10:
                (w.u8 if ft & 0x20 else w.u16)(f.field_id)
            else:
                w.u32(f.field_id)


def _read_function(r: _R) -> FunctionInfo:
    lt = r.u8()
    begin = r.u64() if lt & 1 else r.u32()
    if lt & 2:
        end = r.u64() if lt & 4 else r.u32()
    else:
        end = begin + r.u16()
    sn = r.string() if lt & 8 else None
    fn = r.string() if lt & 0x10 else None
    fid = r.u64() if lt & 0x20 else r.u32()
    return FunctionInfo(fid, begin, end, sn, fn)


def _write_function(w: _W, f: FunctionInfo):
    lt = 0
    if f.begin > 0xFFFFFFFF:
        lt |= 1
    if f.end < f.begin or (f.end - f.begin) > 0xFFFF:
        lt |= 2
        if f.end > 0xFFFFFFFF:
            lt |= 4
    if f.short_name:
        lt |= 8
    if f.full_name:
        lt |= 0x10
    if f.id > 0xFFFFFFFF:
        lt |= 0x20
    w.u8(lt)
    (w.u64 if lt & 1 else w.u32)(f.begin)
    if lt & 2:
        (w.u64 if lt & 4 else w.u32)(f.end)
    else:
        w.u16(f.end - f.begin)
    if lt & 8:
        w.string(f.short_name)
    if lt & 0x10:
        w.string(f.full_name)
    (w.u64 if lt & 0x20 else w.u32)(f.id)


def _read_global(r: _R) -> GlobalInfo:
    lt = r.u8()
    begin = r.u64() if lt & 1 else r.u32()
    sn = r.string() if lt & 2 else None
    tn = r.string() if lt & 4 else None
    gid = r.u64() if lt & 8 else r.u32()
    return GlobalInfo(gid, begin, sn, tn)


def _write_global(w: _W, g: GlobalInfo):
    lt = 0
    if g.begin > 0xFFFFFFFF:
        lt |= 1
    if g.short_name:
        lt |= 2
    if g.type_name:
        lt |= 4
    if g.id > 0xFFFFFFFF:
        lt |= 8
    w.u8(lt)
    (w.u64 if lt & 1 else w.u32)(g.begin)
    if lt & 2:
        w.string(g.short_name)
    if lt & 4:
        w.string(g.type_name)
    (w.u64 if lt & 8 else w.u32)(g.id)


def _read_registration(r: _R) -> Registration:
    lt = r.u8()
    iid = r.u16() if lt & 1 else r.u32()
    impl = r.u16() if lt & 2 else r.u32()
    vt = r.i32() if lt & 4 else -1
    off = 0
    if lt & 8:
        if lt & 0x10:
            off = r.u8()
        elif lt & 0x20:
            off = r.u16()
        else:
            off = r.i32()
    return Registration(iid, impl, vt, off)


def _write_registration(w: _W, g: Registration):
    lt = 0
    if g.interface_id <= 0xFFFF:
        lt |= 1
    if g.implementation_id <= 0xFFFF:
        lt |= 2
    if g.vtable_offset >= 0:
        lt |= 4
    if g.offset_in_type != 0:
        lt |= 8
        if g.offset_in_type >= 0:
            if g.offset_in_type <= 0xFF:
                lt |= 0x10
            elif g.offset_in_type <= 0xFFFF:
                lt |= 0x20
    w.u8(lt)
    (w.u16 if lt & 1 else w.u32)(g.interface_id)
    (w.u16 if lt & 2 else w.u32)(g.implementation_id)
    if lt & 4:
        w.i32(g.vtable_offset)
    if lt & 8:
        if lt & 0x10:
            w.u8(g.offset_in_type)
        elif lt & 0x20:
            w.u16(g.offset_in_type)
        else:
            w.i32(g.offset_in_type)


def parse(data: bytes) -> Library:
    """Parse a raw (decompressed) stream. Stream version 1 (the 2019 releases,
    stored uncompressed) uses the same record layouts; the v14 framework only
    accepts version 2 inside GZip, which is what `write` produces."""
    r = _R(data)
    lib = Library()
    version = r.i32()
    if version < 1 or version > STREAM_VERSION:
        raise ValueError("unsupported stream version %d" % version)
    lib.library_version = r.i32()
    lib.file_version = tuple(r.i32() for _ in range(4))
    if r.u8() != 0:
        lib.alias = tuple(r.i32() for _ in range(4))
        return lib
    lib.library_base_offset = r.u64()
    lib.hash_version = r.u64()
    lib.types = [_read_type(r) for _ in range(r.i32())]
    lib.functions = [_read_function(r) for _ in range(r.i32())]
    lib.globals = [_read_global(r) for _ in range(r.i32())]
    lib.registrations = [_read_registration(r) for _ in range(r.i32())]
    for _ in range(r.i32()):
        lt = r.u8()
        lcount = r.i32() if lt & 1 else r.u8()
        if lt & 2:
            lid = r.u16()
        elif lt & 4:
            lid = r.u8()
        else:
            lid = r.u32()
        ls = []
        for _ in range(lcount):
            jt = r.u8()
            begin = end = None
            if jt & 1:
                begin = r.u8() if jt & 2 else (r.u16() if jt & 4 else r.i32())
            if jt & 8:
                end = r.u8() if jt & 0x10 else (r.u16() if jt & 0x20 else r.i32())
            tid = r.u16() if jt & 0x40 else (r.u32() if jt & 0x80 else r.u64())
            ls.append(TypeInstance(begin, end, tid))
        lib.type_instances[lid] = ls
    count = r.i32()
    did = 0
    while did < count:
        nx = r.i32()
        if nx > 0:
            lib.cached_values.extend(r.i32() for _ in range(nx))
        else:
            nx = -nx
            lib.cached_values.extend([None] * nx)
        did += nx
    if not r.eof():
        raise ValueError("%d trailing bytes" % (len(r.b) - r.p))
    return lib


def raw_bytes(path) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def read(path) -> Library:
    return parse(raw_bytes(path))


def to_bytes(lib: Library) -> bytes:
    w = _W()
    w.i32(STREAM_VERSION)
    w.i32(lib.library_version)
    for v in lib.file_version:
        w.i32(v)
    if lib.alias is not None:
        w.u8(1)
        for v in lib.alias:
            w.i32(v)
        return bytes(w.out)
    w.u8(0)
    w.u64(lib.library_base_offset)
    w.u64(lib.hash_version)
    w.i32(len(lib.types))
    for t in lib.types:
        _write_type(w, t)
    w.i32(len(lib.functions))
    for f in lib.functions:
        _write_function(w, f)
    w.i32(len(lib.globals))
    for g in lib.globals:
        _write_global(w, g)
    w.i32(len(lib.registrations))
    for g in lib.registrations:
        _write_registration(w, g)
    w.i32(len(lib.type_instances))
    for lid, ls in lib.type_instances.items():
        lt = 0
        if len(ls) > 0xFF:
            lt |= 1
        if lid <= 0xFF:
            lt |= 4
        elif lid <= 0xFFFF:
            lt |= 2
        w.u8(lt)
        (w.i32 if lt & 1 else w.u8)(len(ls))
        if lt & 2:
            w.u16(lid)
        elif lt & 4:
            w.u8(lid)
        else:
            w.u32(lid)
        for x in ls:
            jt = 0
            if x.begin is not None:
                jt |= 1
                if 0 <= x.begin <= 0xFF:
                    jt |= 2
                elif 0 <= x.begin <= 0xFFFF:
                    jt |= 4
            if x.end is not None:
                jt |= 8
                if 0 <= x.end <= 0xFF:
                    jt |= 0x10
                elif 0 <= x.end <= 0xFFFF:
                    jt |= 0x20
            if x.type_id <= 0xFFFF:
                jt |= 0x40
            elif x.type_id <= 0xFFFFFFFF:
                jt |= 0x80
            w.u8(jt)
            if jt & 1:
                (w.u8 if jt & 2 else (w.u16 if jt & 4 else w.i32))(x.begin)
            if jt & 8:
                (w.u8 if jt & 0x10 else (w.u16 if jt & 0x20 else w.i32))(x.end)
            (w.u16 if jt & 0x40 else (w.u32 if jt & 0x80 else w.u64))(x.type_id)
    cv = lib.cached_values
    w.i32(len(cv))
    i = 0
    while i < len(cv):
        j = i
        if cv[i] is not None:
            while j < len(cv) and cv[j] is not None:
                j += 1
            w.i32(j - i)
            for v in cv[i:j]:
                w.i32(v)
        else:
            while j < len(cv) and cv[j] is None:
                j += 1
            w.i32(-(j - i))
        i = j
    return bytes(w.out)


def write(path, lib: Library):
    raw = to_bytes(lib)
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:
            g.write(raw)


def summary(lib: Library) -> str:
    return ("library version %d, file version %s, alias %s\n"
            "library base 0x%X, hash 0x%016X\n"
            "types %d (with vtable %d, with size %d, with fields %d)\n"
            "functions %d (with VID %d, named %d), globals %d (named %d)\n"
            "registrations %d, type-instance lists %d, cached values %d (non-null %d)" % (
                lib.library_version, ".".join(map(str, lib.file_version)), lib.alias,
                lib.library_base_offset, lib.hash_version,
                len(lib.types), sum(1 for t in lib.types if t.vtable), sum(1 for t in lib.types if t.size is not None),
                sum(1 for t in lib.types if t.fields),
                len(lib.functions), sum(1 for f in lib.functions if f.id), sum(1 for f in lib.functions if f.short_name),
                len(lib.globals), sum(1 for g in lib.globals if g.short_name),
                len(lib.registrations), len(lib.type_instances),
                len(lib.cached_values), sum(1 for v in lib.cached_values if v is not None)))


if __name__ == "__main__":
    import sys
    orig = raw_bytes(sys.argv[1])
    lib = parse(orig)
    print(summary(lib))
    rt = to_bytes(lib)
    print("round-trip identical: %s (%d vs %d bytes)" % (rt == orig, len(rt), len(orig)))
