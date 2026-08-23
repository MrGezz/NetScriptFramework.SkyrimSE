# Porting the .NET Script Framework to a new Skyrim SE runtime

Everything in this folder exists to answer one question: **can the 2020 framework be moved
off Skyrim SE 1.5.97 without IDA and without meh321?** The answer is a qualified yes, and
these are the tools that do it. Nothing here changes what the framework or its plugins *do* —
it only re-derives the numbers they are compiled against.

Read `..\PORTING-1.7.99.md` first: it is the inventory of what the two repos contain and what
1.7.99 breaks. This file is the mechanism.

## What actually has to move

The framework's C# and C++/CLI code is version-independent. Three sets of numbers are not:

| | Where | How many | Recovered by |
| --- | --- | --- | --- |
| **VIDs** — version-independent ids for functions and globals | `Vids.h` (301), `Implementations.cs` (231), the 18 plugins (250); 536 distinct | `fndiff.py`, which carries Address Library ids from an old executable to a new one | `nsfport.py` |
| **Type identity** — which vtable belongs to which of the 14,453 generated implementation classes, and where each base sub-object sits | the version library's type / registration / type-instance records | the new executable's own MSVC RTTI, matched by demangled class name | `msvcrtti.py` + `nsfport.py` |
| **Cached member offsets** — 325 slots the bindings read instead of hard-coding | the version library's cached-values array; 85 are actually used | CommonLibSSE's 1.7.99 headers, transcribed by hand | `CACHED_VALUES_1_7_99` in `nsfport.py` |

Everything else in the library file (function begin/end, names) is mechanical.

The one thing no tool can recover is the **structure layouts baked into
`Implementations.cs`** — 17,788 literal `this.Address + 0x…` offsets generated from 1.5.97.
AE moved many of them (`TESObjectREFR` 0x98 → 0xA0, `Actor` 0x2B0 → 0x2B8, `PlayerCharacter`
0xBE0 → 0x12D8, every `IMenu` 0x30 → 0x40, `NiAVObject` 0x110 → 0x138 …). See
"Layouts" below.

## The files

| File | What it is |
| --- | --- |
| `nsfbin.py` | Reader/writer for `NetScriptFramework.<Game>.<ver>.bin`, transcribed from `NetScriptFramework\Framework\GameInfo.cs` (stream version 2, GZip, .NET `BinaryWriter` records). Round-trips byte-identically. |
| `msvcrtti.py` | MSVC RTTI reader for a PE image: every vtable, its class, and the class's base-class offsets, with names demangled through `dbghelp!UnDecorateSymbolName`. 7,211 classes / 8,637 vtables in 1.7.99, 0.4 s. |
| `nsfport.py` | Builds the version library for a new executable out of an `fndiff` report, the new image's RTTI, `Implementations.cs`, and the cached-value table. |
| `nsfhooks.py` | Re-checks every byte-pattern-guarded hook site (26 in `Events.h`, 44 in the plugins) against the new executable at the translated address, and where the pattern fails, rescans the containing new function for it — a unique hit is a proposed new offset. This is the go/no-go test. |

They depend on `..\..\AddressLibraryManager\DiffCalculator` (`pe.py`, `fnhash.py`, `fndiff.py`,
`addrlib.py`, `relib.py`) and are plain Python 3, no packages.

## Running it

1. **Carry the ids.** 1.5.97 is the framework's ID space, so the diff is 1.5.97 → target.
   The 1.5.97 executable must be unpacked (SteamStub) first.

   ```
   python ..\..\AddressLibraryManager\DiffCalculator\fndiff.py ^
       SkyrimSE-1.5.97.unpacked.exe version-1-5-97-0.bin SkyrimSE.exe ^
       --out <dir> --cache <cache> --gt gt_1597_1799.tsv
   ```

   `gt_ng.py` (in `DiffCalculator`) builds that ground-truth file out of CommonLibSSE-NG's
   `RELOCATION_ID(se, ae)` pairs — 9,134 hand-verified SE↔AE ids, 8,292 of which resolve in
   both published libraries. It is the only independent check available for this direction.

2. **Build the library.**

   ```
   python nsfport.py --exe SkyrimSE.exe --old-exe SkyrimSE-1.5.97.unpacked.exe ^
       --report <dir>\report.tsv --cache <cache> ^
       --al versionlib-1-7-99-0.bin --rename ..\..\AddressLibraryDatabase\skyrimae.rename ^
       --sizes sizes.tsv --vids all_vids.txt ^
       --out NetScriptFramework.SkyrimSE.1_7_99_0.bin --report-out port-report.txt
   ```

   The file name matters: `Main.LoadGameInfo` builds it from
   `Memory.GetMainModuleVersion()`, so it must be `NetScriptFramework.SkyrimSE.<a_b_c_d>.bin`
   next to `NetScriptFramework.SkyrimSE.dll` in `Data\NetScriptFramework`.

3. **Verify — and re-locate — the hook sites.**

   ```
   python nsfhooks.py --exe SkyrimSE.exe --report <dir>\report.tsv --cache <cache> ^
       --old-exe SkyrimSE-1.5.97.unpacked.exe --old-al version-1-5-97-0.bin ^
       --out out\hooks-1799.tsv
   ```

   Each site gets one of five verdicts — `ok`, `relocate` (a unique match elsewhere in the new
   function, with the proposed offset), `ambiguous`, `gone`, `unresolved-vid` — plus whether
   the pattern still matched at the *old* address, which separates a bad translation from a
   hook site the compiler moved. Nothing is rewritten: the TSV is a work list.

4. **Build the framework.** `..\NetScriptFramework.SkyrimSE.sln` (added here) builds all four
   projects; they were retargeted from `v140` / .NET 4.5.2 / SDK 8.1 to `v145` / .NET 4.8 /
   SDK 10.0. The framework core is a separate repository (`meh321/NetScriptFramework`, cloned
   as a sibling); the project reference goes through `$(NSFCoreProject)`, overridable with
   `-p:NSFCoreProject=…`.

   ```
   msbuild NetScriptFramework.SkyrimSE.sln -p:Configuration=Release -p:Platform=x64
   ```

   The C# projects build `AnyCPU`, the two C++/CLI ones `x64`; the solution maps both onto a
   single `x64` configuration. The `MSB3270` architecture warning on
   `Implementations.csproj` is expected and harmless (an `AnyCPU` assembly referencing an
   `x64` one, which is how the original solution was arranged too).

5. **Version gate.** `SkyrimSEGame::VersionLibraryHash` in `..\NetScriptFramework.SkyrimSE\Game.h`
   is `0x14AEB93E09DDA87F` and is compared against the library's own `HashVersion`; keep the
   two equal (`--hash`) or the framework refuses to load. `IsValidVersion` already returns
   `true` for any version. `LibraryVersion` (14) is the plugin ABI and must not change unless
   the bindings change.

## Layouts — the part that is not automatic

`Implementations.cs` is 557,995 generated lines holding 17,788 literal member offsets and
14,453 `Cast<T>` tables, all from 1.5.97. Anniversary Edition inserted members in the common
base classes, so a large share of those literals is wrong on 1.6.x and 1.7.99. The size table
extracted from CommonLibSSE / CommonLibSSE-NG (`sizes_se_ae.json`, 2,389 classes, 128 of them
with two different sizes) and NG's own `RelocateMemberIfNewer` table (138 SE→AE member
relocations across 76 classes) say which classes moved, but neither is a complete field map,
and NG covers only the classes its users needed.

What `nsfport.py` *does* fix automatically: base-class sub-object offsets (from the new RTTI),
so `Cast<T>` and `FromAddress<T>` land on the right sub-object, and the 85 cached values that
are used. What it does **not** fix: an ordinary field literal inside a class that grew. Those
have to be regenerated from a structure description — CommonLibSSE's headers are the only
public one — or accepted as broken for the classes nobody touches.

Practical consequence: the framework and the ID-driven plugins (hooks, `InvokeCdecl`,
`GetAddressOf`) can be made correct with what is here. The *object model* is correct only for
classes whose layout did not move. Treat `Implementations.cs` as the remaining work item, not
as ported.

## Status (2026-08-23, target 1.7.99)

- All four projects **build** on v145 / .NET 4.8.
- `nsfbin.py` round-trips a real library; the generated 1.7.99 library round-trips too.
- ID translation **99.65 %** correct against 8,292 CommonLibSSE-NG ground-truth pairs, but
  only 33 % of the SE id space resolves at all.
- **60 of the 536 VIDs** the layer and the plugins need are unresolved (41 framework,
  19 plugins; the `53890…54172` run is most of it).
- Hook sites: 29 ok, 12 uniquely relocatable, 9 ambiguous, 13 gone, 7 blocked on a VID.
  All 70 still match at their old address, so these are AE changes, not translation errors.
- `Implementations.cs` layouts are **not** ported — see "Layouts" above.

Outputs land in `out\` (git-ignored): the generated library, `hooks-1799.tsv` (the hook
verdicts and proposed offsets), `port-report.txt`, and the inputs that were derived
(`gt_1597_1799.tsv`, `all_vids.txt`, `sizes_1799.tsv`).

`..\PORTING-1.7.99.md` §5 has the full write-up and the cost estimate.

## Licence

The framework and its plugins are meh321's, MIT (see the upstream repositories). These port
tools are part of this fork and carry the same licence. Nothing here redistributes any part
of Skyrim.
