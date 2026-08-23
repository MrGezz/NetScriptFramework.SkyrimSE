# .NET Script Framework (SkyrimSE) and its plugins on Skyrim SE 1.7.99

Measured 2026-08-23 on the checkouts in this workspace (`NetScriptFramework.SkyrimSE`,
`NetScriptFramework.Plugins`, both meh321's 2020-12-04 source drops). Nothing here has been
ported yet; this is the inventory and the plan.

## 1. What the two repos contain

**NetScriptFramework.SkyrimSE** — the game layer, C++/CLI (`CLRSupport=true`, PlatformToolset
`v140`), plus the generated C# bindings:

| File | Lines | Role |
| --- | --- | --- |
| `Vids.h` | 1,437 | 302 distinct Address Library IDs (604 references) — **all in the Skyrim SE 1.5.97 ID space** |
| `Game.h` / `Game.cpp` | 373 / 535 | `SkyrimSEGame`: `AppVersion`, `IsValidVersion`, `LibraryVersion`, `VersionLibraryHash` — aborts the game on any other executable |
| `Events.h` | 1,472 | the `Events.On*` hook points the plugins subscribe to |
| `Module.h` | 635 | module/memory helpers |
| `..\NetScriptFramework.SkyrimSE.Implementations\Implementations.cs` | 557,995 | generated struct/field/function bindings with 1.5.97 offsets |

The `.vcxproj` references `..\NetScriptFramework\NetScriptFramework.csproj` — the framework
core (`github.com/meh321/NetScriptFramework`, branch head `12459275`), which is **not checked
out** in this workspace. The plugins build against the prebuilt `Resources\NetScriptFramework.dll`
and `Resources\NetScriptFramework.SkyrimSE.dll` instead.

**NetScriptFramework.Plugins\SkyrimSE\Plugins** — 18 C# plugins, 34,398 lines in 142 files, no
READMEs. Counts below are `grep` counts (WH = `Memory.WriteHook`, RP = `Memory.ReadPointer`,
IC = `Memory.InvokeCdecl`, VIDs = distinct `GetAddressOf`/wrapper literals, see §4 for the
regexes):

| Plugin | Lines | Purpose | Hooks / VIDs | Byte patches | NSF events | INI keys |
| --- | --- | --- | --- | --- | --- | --- |
| ActorLimitPlugin | 257 | raise actor movement / facial-morph update caps | WH 6 · 6 VIDs | 4× `WriteInt32`, 4× `WriteUInt8`, static buffer | – | 3 |
| BetterStealing | 1,268 | decaying "stolen" flag, stolen-stack count fix | 5 hooks, RP 6, IC 6 · 13 (+8 caller IDs) | 1× `WriteBytes` | OnFrame, OnMainMenu | 8 |
| BetterTelekinesis | 6,426 | multi-object telekinesis overhaul | **WH 32**, RP 7, IC 21 · 42 | 13× `Allocate`, 3× `WriteBytes`, 4× `WriteNop`, 5× `WritePointer`, 8× `WriteUInt32` | OnFrame, OnMainMenu | 64 |
| BlinkSpell | 1,734 | aimed short-range teleport spell | RP 3, IC 7 · 5 | 1× `WriteBytes`, 4× `WriteFloat` | OnFrame, OnMainMenu, OnUpdateCamera, OnMagicCasterFire | 25 |
| BugFixesSSE | 638 | ability-condition re-eval, buy/sell speech XP, SpeedMult update | WH 5, RP 2, IC 2 · 9 | 1× `WriteNop`, 1× `WritePointer`, 1× `WriteUInt8` | OnFrame | 4 |
| CraftingSkill | 764 | configurable temper/enchant/alchemy XP formulas | 5 hooks, RP 13, IC 3 · 8 | – | – | 33 |
| CustomSkills | 1,644 | custom skill trees + perk menus from config files | 30 hooks, RP 7, IC 13 · 33 | 6× `WriteBytes`, 7× `WritePointer` | OnFrame, OnMainMenu | 20 + 8×129 per skill |
| DebugConsole | 457 | WinForms debug output window | none · 0 | – | – | 0 |
| GamePlayTweaks | 4,457 | 17 switchable gameplay tweaks | WH 14, RP 2, IC 8 · 26 | 4× `WriteBytes`, 5× `WriteNop` | OnFrame, OnMainMenu, OnGainSkillXP, OnGainLevelXP, OnCalculateFormGoldValue, OnWeaponFireProjectilePosition, OnUpdatedPlayerHeadtrack, OnUpdateCamera, OnCalculateDetection | 36 |
| GrassControl | 3,648 | grass cache (`Data/Grass/*.gid`), distant grass, ray-cast culling | **WH 50**, RP 34, IC 39 · **62** | 3× `WriteBytes`, 4× `WriteNop`, 17× `WriteUInt8`, 12× `WriteInt32` | OnMainMenu, OnFrame | 19 |
| IFPV | 9,150 | Immersive First Person View | 18 hooks + 14 function lookups, RP 6, IC 6 · 28 | 3× `WriteUInt8` | OnFrame, OnMainMenu, OnUpdateCamera, OnUpdatePlayerTurnToCamera, OnUpdatedPlayerHeadtrack, OnWeaponFireProjectilePosition, OnShadowCullingBegin/End | 87 |
| InfiniteArrows | 279 | infinite ammo with gating | WH 1 · 1 | 2× `WriteUInt8` | OnSpendAmmo, OnReduceHUDAmmoCounter | 8 |
| InfinitePoison | 246 | poison not consumed on hit | WH 1, IC 1 · 2 | – | OnSpendPoison | 7 |
| ItemDurability | 2,040 | weapon/armour degradation | **WH 21**, RP 6, **IC 25** · 33 | 3× `WriteBytes`, 4× `WriteUInt8` | OnFrame, OnMainMenu, OnWeaponFireProjectilePosition | 28 |
| NoLockPicking | 602 | instant unlock consuming picks by difficulty | WH 2, RP 8, IC 9 · 14 (`CachedVid`) | 3× `WritePointer`, 2× `WriteZero` | – | 11 |
| UninterruptedEtherealForm | 170 | Ethereal Form not cancelled by actions | none · 0 | – | OnRemoveMagicEffectsWithArchetype, OnCalculateDetection | 2 |
| UninterruptedInvisibility | 170 | same for Invisibility | none · 0 | – | same | 2 |
| WeaponCharge | 448 | enchanted weapons recharge from carried soul gems | IC 14 · 11 | – | OnFrame, OnMainMenu | 9 |

277 distinct VIDs across the plugins (GrassControl 62, BetterTelekinesis 42, CustomSkills 33,
ItemDurability 33, IFPV 28, GamePlayTweaks 26, …). Two plugins additionally identify *callers*
by VID at runtime (`GameInfo.GetFunctionInfo`): BetterStealing (8 cases) and IFPV (4 cases).

## 2. What 1.7.99 breaks

1. **ID space.** The Anniversary-Edition Address Library (`..\AddressLibraryDatabase\skyrimae.relib`,
   1.6.317 → 1.6.1179, IDs up to 522,614) is a different database from the 1.5.97 one. None of
   NSF's 302 + 277 IDs mean anything in a 1.6.x/1.7.x bin. The only local SE→AE table is
   CommonLibSSE-NG's `RELOCATION_ID(se, ae)` macros (`..\CommonLibSSE-NG\include`, 8,712 SE IDs):
   it covers **34 of 302** `Vids.h` IDs and **21 of 277** plugin IDs. The rest have to be derived
   from the binaries: `..\AddressLibraryManager\DiffCalculator\fndiff.py` can match the 1.5.97
   executable against 1.7.99 the same way it matches 1.6.1170 against 1.7.99, which yields
   *SE ID → 1.7.99 offset* and, joined with the 1.7.99 AE bin, *SE ID → AE ID*. Inputs: the
   unpacked 1.5.97 executable (`D:\tmp\icz-build\exe\SkyrimSE-1.5.97.exe.unpacked.exe`, from
   the Recycle-Bin copy; the original is SteamStub-encrypted like 1.6.1170) and
   `versionlib-1-5-97-0.bin` from the SE Address Library — **not on this machine yet**.
2. **Structure layouts.** `Implementations.cs` and `Game.h` hard-code 1.5.97 field offsets.
   1.6.629+ changed engine structures (SKSE's `kVersionIndependent_StructsPost629`;
   CommonLibSSE asserts `sizeof(Actor)` 0x2B0 → 0x2B8, `PlayerCharacter` 0xBE0 → 0xBE8,
   `TESObjectREFR` 0x98 → 0xA0 — `..\CommonLibSSE\include\RE\A\Actor.h:739-741`,
   `RE\P\PlayerCharacter.h:518-520`, `RE\T\TESObjectREFR.h:478-480`). Every binding that
   reads a shifted field must be regenerated from AE layouts; 1.7.99 adds its own changes
   (po3 CommonLibSSE `4b919eaa` "some initial 1.7.99 changes": `PlayerCharacter.h`,
   `SkyrimVM.h`, `BSSystemEvent.h`).
3. **Byte patches.** Every `WriteBytes`/`WriteNop`/`WriteUInt8`/`WriteInt32` site above was
   written against 1.5.97 instruction bytes (most guard with a byte-pattern string); each has to
   be re-verified against the 1.7.99 code at the translated address.
4. **Version gate.** `SkyrimSEGame.IsValidVersion` / `VersionLibraryHash` must accept 1.7.99 and
   the new bin; the plugins' `RequiredLibraryVersion` (10–14) stays.
5. **Toolchain.** `v140` C++/CLI projects and the missing core. Visual Studio 2026 here has
   MSVC 14.44 and 14.51 and .NET Framework 4.8 reference assemblies; `/clr` for .NET Framework
   still builds, but the solution needs the core repo and a retarget.

## 3. Recommended path

**A. Keep NSF alive (framework port).** Clone `meh321/NetScriptFramework` beside these repos,
retarget everything to `v145` / net48, generate the SE→AE ID translation (step 2.1), rebuild
`Vids.h` in the AE ID space, regenerate `Implementations.cs` from AE layouts (CommonLibSSE's
headers are the reference), re-verify every byte patch, lift the version gate. This is the same
amount of work CommonLibSSE-NG needed for SE/AE dual support — weeks, not days — and it only
pays off if writing plugins in C# is the point.

**B. Port the plugins to SKSE + CommonLibSSE (po3) as native plugins.** The community already
did this for the most-used ones; the MO2 `D:\TEMPEST` instance has them installed and their
`SKSEPlugin_Version` data (read 2026-08-23) marks them Address-Library independent, so they
run on 1.7.99 as soon as `versionlib-1-7-99-0.bin` exists:

| NSF plugin | SKSE equivalent already in `D:\TEMPEST\mods` |
| --- | --- |
| ActorLimitPlugin | `ActorLimitFix.dll` |
| BugFixesSSE | `BugFixesSSE.dll` (SKSE port) |
| GrassControl | `NGIO-NG.dll` (No Grass In Objects NG) |
| CustomSkills | `CustomSkills.dll` (Custom Skills Framework) |
| IFPV | `ImprovedCameraSE.dll` |
| NoLockPicking | `No Lockpick Activate.dll` |
| DebugConsole | `po3_ConsolePlusPlus.dll` / `MoreInformativeConsole.dll` (different scope) |
| BetterStealing | `SureOfStealing.dll` (different design, not a port) |

No equivalent installed: **BetterTelekinesis, BlinkSpell, CraftingSkill, GamePlayTweaks,
InfiniteArrows, InfinitePoison, ItemDurability, UninterruptedEtherealForm,
UninterruptedInvisibility, WeaponCharge.** Port order by effort: the four event-only /
single-hook plugins first (Uninterrupted×2, InfiniteArrows, InfinitePoison — a day each with
CommonLibSSE event sinks), then WeaponCharge, CraftingSkill, NoLockPicking-style hooks, then
ItemDurability (21 hooks), BlinkSpell, and last GamePlayTweaks (17 tweaks, 14 hooks) and
BetterTelekinesis (32 hooks, 13 trampolines).

Recommendation: **B**, with A only if C# plugin authoring is a requirement. Either way the
first prerequisite is the same: a 1.7.99 Address Library bin (being produced by
`DiffCalculator`) and, for anything that still needs the SE IDs, `versionlib-1-5-97-0.bin`.

## 4. How the numbers were obtained

- Lines: `find <plugin> -name "*.cs" -exec cat {} \; | wc -l` (includes `AssemblyInfo.cs`).
- Hooks: `grep -roh "Memory\.WriteHook" --include=*.cs | wc -l` (likewise `ReadPointer`,
  `InvokeCdecl`, `GetAddressOf`).
- VIDs: `GetAddressOf\(\s*[0-9]+` plus each plugin's wrapper (`gi(` in BetterTelekinesis,
  `InstallHook(`/`PrepareFunction(` in IFPV, CraftingSkill, BetterStealing, `WriteHook(` in
  CustomSkills, `CachedVid.Initialize(` in NoLockPicking, `vid = ` in GamePlayTweaks and
  BugFixesSSE), `sort -nu`.
- Byte patches: `Memory\.Write(Bytes|Nop)`, `Memory\.Allocate`, `new byte\[\]`.
- Events: `grep -roh -E "Events\.[A-Za-z]+"`.
- SE→AE coverage: `(RELOCATION_ID|RelocationID|VariantID)\(\s*(\d+)\s*,\s*(\d+)` over
  `CommonLibSSE-NG\include` intersected with the numeric literals of `Vids.h` and the plugin VIDs.

## 5. What was actually done (2026-08-23)

Path **A** (keep NSF alive) was taken far enough to say what it costs. Everything below is in
`Port\` — see `Port\README.md` for how to run it.

**Toolchain.** The framework core (`meh321/NetScriptFramework`, HEAD `1245927`, unchanged since
2020-12-04) is now cloned as a sibling repository. All four projects were retargeted from
`v140` / .NET 4.5.2 / Windows SDK 8.1 to **v145 / .NET 4.8 / SDK 10.0** and build clean under
Visual Studio 2026:

    NetScriptFramework.dll                        273 KB   AnyCPU
    NetScriptFramework.Runtime.dll                         x64, C++/CLI
    NetScriptFramework.SkyrimSE.dll               1.1 MB   x64, C++/CLI
    NetScriptFramework.SkyrimSE.Implementations.dll 10 MB  AnyCPU

A solution (`NetScriptFramework.SkyrimSE.sln`) maps the C# `AnyCPU` and C++/CLI `x64`
configurations onto one `x64` build; the core project path is the overridable
`$(NSFCoreProject)`. Only warnings: three `CS0414` in the core and the expected `MSB3270`
architecture mismatch on the bindings.

**Version library.** `Port
sfbin.py` implements the `NetScriptFramework.<Game>.<ver>.bin`
format (stream version 2, GZip, .NET `BinaryWriter` records) from `GameInfo.cs`, and
`Port
sfport.py` builds one for a new executable. Result for 1.7.99:

| | |
| --- | --- |
| functions | 230,063 (88,771 carry a VID, 32,525 named from `skyrimae.rename`) |
| globals | 170,095 |
| types | 14,453 (5,803 with a vtable from the new image's RTTI, 1,340 with a size from CommonLibSSE) |
| registrations / type-instance lists | 15,685 / 5,803 |
| cached values | 80 of the 325 slots filled (85 are actually read by the bindings) |

The ID translation is `fndiff.py` run 1.5.97 → 1.7.99 (the framework's ID space is the SE one).
Scored against 8,292 `RELOCATION_ID(se, ae)` pairs mined out of CommonLibSSE-NG by the new
`DiffCalculator\gt_ng.py`: **99.65 % correct** (8,237 right, 29 wrong, 26 unresolved). Coverage
is the weak point, not accuracy — only 258,903 of 778,674 SE ids resolve at all, because AE
recompiled most of the executable.

Type identity is recovered from the executable's own MSVC RTTI (`Port\msvcrtti.py`: 7,211
classes / 8,637 vtables in 1.7.99, demangled through `dbghelp`), matched to the generated
bindings by canonical class name, with ambiguous template twins settled against the 1.5.97
base-class layout the bindings were generated from. 5,803 of 6,879 virtual implementations
match; the 1,076 that do not are anonymous-namespace classes and the `bnet` (Bethesda.net)
tree, which get vtable-less registrations.

**The two things that block a working build.**

1. **60 of the 536 VIDs the layer and plugins need do not resolve.** 41 belong to the framework
   (`Vids.h`), 19 to plugins. The cluster `53890…54172` is the bulk of it — a run of adjacent
   `Actor`/`TESObjectREFR` functions whose surroundings changed too much for hash, callgraph or
   locality matching. Each needs a hand-derived address.

2. **Interior hook offsets do not survive.** Function *starts* translate; the hooks do not use
   starts. `Port
sfhooks.py` re-checks every byte-pattern-guarded site and, where the pattern
   fails, rescans the containing new function for it. Of 70 sites:

   | verdict | count | meaning |
   | --- | --- | --- |
   | ok | 29 | pattern still matches at the translated offset |
   | relocate | 12 | unique match elsewhere in the new function — proposed offset in `Port\out\hooks-1799.tsv` |
   | ambiguous | 9 | pattern occurs several times in the new function; needs a human |
   | gone | 13 | pattern absent — the compiler changed the instruction sequence |
   | unresolved-vid | 7 | the VID itself did not translate (see 1) |

   Every one of the 70 matches at its **old** address, so these are genuine AE changes, not
   translation errors. The 128 `WriteHook(new HookParameters …)` sites that carry no pattern
   cannot be checked this way at all and have to be inspected by hand.

Beyond those, `Implementations.cs` still holds 17,788 literal member offsets generated from
1.5.97, and AE moved a large share of them (`TESObjectREFR` 0x98 → 0xA0, `Actor` 0x2B0 → 0x2B8,
`PlayerCharacter` 0xBE0 → 0x12D8, every `IMenu` 0x30 → 0x40, `NiAVObject` 0x110 → 0x138 …;
`Port\out\sizes_1799.tsv` and the SE/AE size table list 128 classes whose size changed).
`nsfport.py` fixes the base-class sub-object offsets, so `Cast<T>` and `FromAddress<T>` land
correctly, but not ordinary field literals inside a class that grew. The object model is
therefore correct only for classes whose layout did not move.

**Verdict, unchanged from §3 but now measured:** reviving the framework is real work — call it
a week of hand-derivation for the 60 VIDs and 29 hook sites, plus regenerating
`Implementations.cs` from a structure description if the object model has to be trustworthy.
Recommendation **B** (port the individual plugins to SKSE + CommonLibSSE) still costs less for
the same result, unless writing plugins in C# is itself the point. What is in `Port\` removes
the mechanical two thirds of A either way, and is reusable for the next runtime.

## Upstream status (checked 2026-08-23)

No update anywhere. `meh321/NetScriptFramework` (core), `meh321/NetScriptFramework.SkyrimSE` and
`meh321/NetScriptFramework.Plugins` all still end at the 2020-12-04 "Files" commits — the same HEADs
this fork carries. The only public forks are `ArranzCNL/.NetScriptFramework` (last commit 2020-03-01,
releases up to 1.5.97) and `KernalsEgg/NetScriptFramework` (last commit 2021-10-12, KernalsEgg's own
ScrambledBugs work, still 1.5.97). Nothing targets 1.6.x, let alone 1.7.99, so any AE/1.7 revival is
new work: the core repo would have to be cloned (not in this workspace), moved off the 1.5.97 ID
space (see above) and taught the format-5 Address Library, before a single plugin can be rebuilt.
