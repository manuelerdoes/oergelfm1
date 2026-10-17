# CLAUDE.md — oergelfm1

Personal fork of **Felucca** (multi-engine synth firmware for the M-VAVE FM-1, upstream
`hugelton/Felucca`, GPL-3.0-only), at Felucca 1.6.1. Remote: `manuelerdoes/oergelfm1`.
Owner: Manuel Erdös. Read this file first; keep it current when decisions are made.

## The vision (owner's wishes)

Turn the firmware into a **Dracula / dark-magic-world** instrument.

1. **Visual style** fits a dark magical world: palettes (blood red, candle gold, crypt
   violet, bone white on near-black), splash screen, wording where it doesn't hurt usability.
2. **Church organs** (pipe organ: principals, flutes, reeds, full plenum, with a big reverb).
3. **Harpsichord** (plucked, bright, quick decay, optional 8'+4' / lute-stop variants).
4. **Analog-style subtractive synth** with **Moog-style ladder (24 dB) filter** and
   **Korg MS-20-style filter** (aggressive, self-oscillating, screaming resonance).
5. Prefer **themed default patches** over new code wherever the existing engines can do it.
6. **New feature: resampling / merging audio.** Besides the 4 MIDI tracks, one **audio track**
   into which the sound of the other tracks can be bounced ("merged") whenever wanted, then
   played back (and merged into again: overdub-style), freeing up the synth tracks.

Order of work the owner has agreed to: write this file first; nothing implemented yet.

## What already exists (checked 2026-10-15) vs. what is missing

| Wish | Status |
| --- | --- |
| Saving patches | **Exists.** 32 user preset slots (`firmware/src/upreset.c`, SAVE > USER, also the web editor), 4 project slots (`project.c`). Factory presets are `preset_t` tables compiled into each engine (`*_PRESETS[]`). |
| Subtractive synth | **Exists:** ANALOG (`firmware/engines/src/eng_analog.c`): 2 polyBLEP oscs (SAW SQR TRI SIN PWM SYNC SUB), noise, drive, Simper trapezoidal **SVF 12 dB** with TYPE LP/BP/HP (`P_FTYPE`, track param). |
| Moog ladder / MS-20 filter | **Missing.** Only the SVF. Natural place: new `P_FTYPE` values (3 = LADDER, 4 = MS-20), or a new engine. |
| Organ | Partly. WHEEL = tonewheel/Hammond (drawbar registrations, `CHAPL` is the closest to churchy), PHASE `ORGAN`, FM6 `ORGAN` (drawbars). **No pipe/church organ.** |
| Harpsichord | **Missing** entirely. Candidates: FM6 patch (`tools/gen_fm6_patches.py`, DX7-style harpsichords are classic), PHYS `STRNG` model, PHASE. |
| Dark theme | Palettes in `tools/gen_ui_palettes.py` (VIOLET/ROSE are closest). CUSTOM palette exists (set from the web editor). Splash: `ui_draw.c draw_splash`. |
| Audio track / resampling | **Missing.** See constraints below. |

## Hardware & firmware constraints (they shape every design)

- **RAM ~512 KB total:** `.data/.bss` 72 KiB (≥ 4 KiB must stay free), `.pool` 360 KiB
  (≥ 8 KiB must stay free; the delay line alone is 128 KiB). `tools/build.py` enforces both.
- **Flash 1 MB.** Code via XIP ≈ 568 KB. User sample slots: 3 × 80 KB at `0xA0000..0xDBFFF`
  (IMA ADPCM, ≈ 7.4 s at 22.05 kHz each). Flash map comment: top of `firmware/src/storage.c`.
- **No flash erase/program while the transport plays** (XIP stalls; settings/slices only
  write when stopped). A bounce must record to RAM, commit to flash when stopped.
- **CPU:** audio ISR renders blocks of `CTL` (32) samples, 44.1 kHz, 8 voices shared by 4 parts.
  Fixed-point only (Q15/Q13), no floats in the audio path. Second core exists (beta, off by default).
  CPU cost is regression-tested (`tests/cpu_baseline.txt`, `tests/target_budget.txt`).
- **`NTRK = NPART = 4` is baked into project formats, the editor protocol and user presets.**
  The audio track must be a *separate* lane, not a 5th `track_t`.
- **Append-only indices:** `ENGINES[]` order, palette ids, `P_*` (new common params go *just
  before `P_E0`*), `G_*`. Old projects/presets map by count. Never reorder.
- `track_t` size must stay a multiple of 16 (static assert; ISR indexing cost).
- An engine has exactly 8 params (`P_E0..P_E7`); ANALOG's 8 are full.

## Resampling / audio track

**Decided (2026-10-17):**
- **Bounce only while the transport is stopped.** The firmware is not performance-centred.
  So the bounce can be an *offline render*: the main loop runs the sequencer and `mix_block`
  itself, encodes and writes to flash as it goes. RAM stops being the limit (flash writes are
  fine while stopped), and it doesn't have to run in real time.
- **Stereo.**
- **Length: the maximum the hardware allows without sacrificing existing features.**
- Playback: a dedicated audio lane beside the 4 tracks, synced to the sequencer, with
  level/pan/sends. "Merge again" = render lane + tracks into a new bounce.

**Flash budget (1 MiB part, map from `storage.c` / `hal/fm1_flash.h`):**

| Range | Use |
| --- | --- |
| 0x00000–~0x92000 | boot + app code (XIP; real image size known only after a build) |
| ~0x92000–0x96FFF | gap: size unknown until built; possibly a few KB free |
| 0x97000–0x9EFFF | projects |
| 0x9F000 | FM6 user-preset patches (copy A) |
| **0xA0000–0xDBFFF** | **user sample slots USR1..3, 3 × 80 KB** |
| 0xDC000–0xDFFFF | user presets |
| 0xE0000–0xE4FFF | OTA staging |
| 0xE5000–0xE6FFF | autosave A/B |
| 0xE7000 | FM6 voice bank |
| 0xE8000–0xFBFFF | stock firmware's VM backup: **never write** (keeps "Return to official V15" safe) |
| 0xFC000–0xFEFFF | settings, FM6 patches copy B |

The only sizeable space is the user sample slots. **Decided (2026-10-17): bounces are stored
in the user sample slots** (one slot or several consecutive ones), **stereo IMA ADPCM at
22.05 kHz**. User samples stay a feature and share that space with bounces. IMA ADPCM is 4 bit
and already decoded by SAMPLE/SLICE:

| Format | 1 slot (80 KB) | all 3 slots (240 KB) |
| --- | --- | --- |
| stereo 22.05 kHz (~22 KB/s) | ~3.6 s | ~10.9 s |
| stereo 44.1 kHz (~44 KB/s) | ~1.8 s | ~5.4 s |

(At 120 BPM one bar = 2 s.) Still open: whether the lane's settings are saved in projects (a
new project format, FUN11?), the UI entry point.

Possible extra space, to check before relying on it: the 1.6.1 app image is 511,472 B, so code
ends near flash 0x80F10. The data store starts at 0x97000, leaving ~90 KB unused today. It is
the app's room to grow (the linker's XIP region runs to ~0x920DC), so taking any of it for audio
caps future code. Don't use it without the owner's OK.

## Fork vs. upstream

- `firmware/engines/` is a **git submodule** → `hugelton/Felucca-Engines` (**MIT**): ANALOG LOFI
  TRIO WHEEL NOISE + `dsp.c`. **Forked (2026-10-17)** to `manuelerdoes/Felucca-Engines`;
  `.gitmodules` points there, and the submodule has an `upstream` remote → hugelton. The LADDER
  and MS-20 filters go into ANALOG there (as new `P_FTYPE` values). Submodule changes are
  committed (on a branch, not detached HEAD) and pushed in the submodule first, then the new
  pointer is committed here. `gh` is installed and logged in as manuelerdoes.
  Run `git submodule update --init` after cloning.
- Keep the SPDX GPL-3.0-only header on new files in `firmware/src`; MIT in `firmware/engines`.
- Don't change `FELUCCA_ID` (package identity) casually: the installer checks it.

## Build & test

- Build: `./build.sh` (→ `build/felucca.fwsc`). Needs JieLi toolchain (`tools/get_toolchain.sh`
  → `~/.jieli/toolchain`), the AC79 SDK at `~/fw-AC79_AIoT_SDK`, Docker (macOS), Python Pillow +
  fontTools. Full details: `BUILDING.md`.
- **This Mac (2026-10-17):** toolchain, SDK, Colima (Docker engine, Rosetta), Pillow, fontTools,
  Emscripten and `gh` are installed. Colima must be running (`colima start` after a reboot).
- `./build.sh` (~20 s) and `tests/run_tests.sh` work as documented. python.org Python 3.10's Pillow
  needs Homebrew's fribidi for text layout (libraqm). It's linked into `/usr/local/lib`
  (`libfribidi.dylib` → `/opt/homebrew/lib`). If that link goes missing, the font generator fails
  and `tests/run_tests.sh` reports "text spacing: FAIL", an environment problem rather than a
  code bug. `DYLD_*` variables don't help through `build.sh`: macOS strips them via `/bin/sh`.
- **Baseline 1.6.1 (2026-10-17): every test passes**, including the browser emulator tests.
- Baseline 1.6.1 sizes: image 511,472 B; RAM .data+.bss 60,984 / 73,728 B; pool 328,896 /
  368,640 B (≈ 39 KB pool free, 8 KB must stay free).
- The full test suite takes ~10 minutes: run it in the background, log to `build/`.
- Tests: `tests/run_tests.sh` (after `./build.sh`). Golden render hashes of every engine × preset
  in `tests/golden.txt`: **adding or changing any factory preset or DSP changes them** → after an
  intended sound change, `GOLDEN_UPDATE=1 sh tests/run_tests.sh`; `BUDGET_UPDATE=1` for CPU files.
  UI tests render every screen in every palette and lint clipping/overlap/alignment (PNGs in
  `build/ui_new/`) — new palettes and wording must pass them.
- Fast iteration without hardware: the browser emulator (`web/emu/build.sh`, needs Emscripten),
  served with `python3 -m http.server -d build/emu 8790`.
- Install on device: web installer or `python3 tools/fm1_install.py build/felucca.fwsc`. Real-FM-1
  testing is the final check; recovery tool: FM-1-transporter.

## Progress

Agreed order: (1) dark palettes + organ/harpsichord presets → (2) LADDER and MS-20 filters in
ANALOG → (3) the bounce / audio track.

- **Step 1 (2026-10-17, done; full suite green, not yet heard by the owner or tried on hardware):**
  - Palettes DRACULA (the first-boot default), CRYPT, GRIMOIRE, CARFAX: ids 10..13
    (`tools/gen_ui_palettes.py`, `UI_DEFAULT_INDEX`; `panel.c`). Invalid stored ids still fall back to GREY.
  - The splash shows the name in THEME. Renaming the product is still the owner's decision.
  - WHEEL (fork): registrations 16..19 PRINC PLENO GEDKT CORNT; presets 5..9 CATHEDRAL PRINCIPAL
    CRYPT FLUTE CORNET REQUIEM.
  - ANALOG (fork): presets 12..14 HARPSICHORD, LUTE STOP, HARPSI 16. PHASE was tried first and
    dropped: its saw tops out near a 700 Hz centroid on C4, too dull. PHYS STRNG has the right
    timbre but only 3 voices.
  - Knock-on test edits: `tests/ui_test.c` (preset browsing past ANALOG's end, COLOR wrap order),
    `theme_test.c` / `settings_test.c` (palette count), `web/editor.html` mock tables (presets, REG
    names). DRACULA's red is coral rather than deep blood: deeper fails the ink-on-selection
    contrast check (4.5).

- **Samples (2026-10-17):** the owner wants sampled sounds where synths sound too synthetic,
  especially the harpsichord. Decided: **SAMPLE set 3 SAX → HARPSI** (VCSL English harpsichord,
  normal registration, CC0; 5 zones C1..C5, 1.0 s each with a 0.35 s fade; `tools/gen_samples.py`
  CC0_SETS, `tools/fetch_cc0.py HARPSI`). The SAX files are deleted. GRAIN's GLITCH preset (it
  granulated SAX) is removed, so FROZEN and SHIMMER are GRAIN presets 1 and 2 now. Organs stay
  synthetic; the owner will make a better organ preset with the bake button. Pitch checked: all
  zones within 16 cents. Image after the swap: 535,7xx B, so **~46 KB of flash headroom** left
  (XIP region to ~0x920DC).
- **Bake button (2026-10-17):** the owner tweaks factory presets in the emulator, saves them as
  user presets (SAVE > USER) and bakes them into the firmware. **Owner's rule: a tweaked preset
  replaces the factory preset it was made from, not an extra one.** Adding as new is only an option.
  - `python3 tools/emu_server.py` serves `build/emu` on http://localhost:8790/. The page's
    "Bake presets into the firmware…" button (shown only when `/api/ping` answers) lists the user
    presets from the page's saved flash (localStorage). Each row has a target dropdown (default:
    "replaces <its origin>"; any factory preset of the engine; "added as a new preset") and a
    name. The name defaults to the factory name unless the user named the sound (auto names are
    `<ENGINE> NN`). The button bakes the ticked rows, removes ticked baked ones (a replaced
    factory preset comes back), rebuilds firmware and emulator (~30 s) and offers a reload.
  - Origin: `upreset.c up_store_cat` writes the factory preset the sound came from (+1) into the
    record's free byte `packed[UP_ORIGIN_AT]` (142, just before the category byte). It's taken from
    `t->preset`, or inherited from the user preset the track plays. Older firmware ignores it.
  - `tools/bake_presets.py` decodes user preset records (storage.c commit records → upreset.c
    banks), keeps the baked list in `firmware/src/baked_presets.json` (by parameter name, with
    `replaces` index, `factory_name`, `factory_pat`), and generates `firmware/src/baked_presets.h`.
    Every engine's `*_PRESETS[]` ends with an `#ifdef FELUCCA_BAKED_<ENGINE>` hook (MIT engines too;
    SAMPLE's via gen_samples.py, after the PERC alias). The macro appends added presets, then
    overrides replaced ones with C99 designated initializers (`[4] = {...}`; the header silences
    clang's `-Winitializer-overrides`). `BAKED[]` (index or 255 = added) holds the values a
    `preset_t` can't (LFOs, matrix, insert, filter type, exact voice mode); `ui.c apply_preset_to`
    applies them via `engines.c baked_of`. The track's own values (`param_kept`) and the user
    preset's 16-step pattern aren't baked; a replaced preset keeps its factory pattern hint.
  - Not bakeable: FM6 (its patch lives beside the record), DIGITAL. A SAMPLE/GRAIN/SLICE sound
    on USR1..3 bakes the slot reference, not the sample.
  - `tests/bake_e2e.sh` (not in run_tests: it rebuilds twice) makes a user preset from ACID on the
    panel, bakes it, and proves ACID's slot then plays it bit for bit, with the preset count unchanged.
    After a real bake, `GOLDEN_UPDATE=1` updates the renders of replaced presets (expected
    changes); `ui_test.c` (categories) and `web/test_web.mjs` (editor mock) ignore baked presets.
    Other tests that name factory presets (e.g. ACID, HARPSI) may need updating after a replacement
    renames one.
- Fetching CC0 samples: python.org Python lacks root certificates, so use
  `SSL_CERT_FILE=/opt/homebrew/etc/ca-certificates/cert.pem python3 tools/fetch_cc0.py SET`.

- **Step 2 (2026-10-17): LADR and MS20 filters** in ANALOG (engines fork, `eng_analog.c`), filter
  TYPE values 3 and 4 (`params.c N_FTYPE`: LP BP HP LADR MS20):
  - Both are zero-delay-feedback models built from trapezoidal one-poles (`anl_coef`/`anl_tick`),
    G Q14, other coefficients Q12, states clamped to ±60000 so every product fits 32 bits.
    **Each one-pole uses error feedback** (keeps its rounding remainder): without it the fixed-point
    filters stall at low cutoffs (no self-oscillation below ~150 Hz). With it they match a float
    model of the same equations at every cutoff. States: s[0] s[1] s[4] s[5], remainders packed in
    s[6] s[7]; `keep = 0xF3`.
  - LADR: 4 poles, k 0..5 (self-oscillation from RES ~102), tanh at the input, bass loss half
    compensated (`AF_LCOMP`). MS20: Korg-35 (Pirkle), K 0.01..2.3 (self-oscillation from RES ~110),
    tanh inside the loop, passband 1.
  - LP/BP/HP are bit-identical: their loops are unchanged. The new types run in `analog_render_n`
    (two specialised copies of `analog_osc`); SYNC/SUB waves go through them in `analog_render_x`.
    Target cost per loop is about the LP loop's (`tests/target_budget.txt`: analog_render_n 1142
    for both copies). Flash +~4.5 KB (image ~540 KB, ~41 KB headroom).
  - `preset_t` has a new `ftype` field (`FTYPE(n)` macro, 0 = LP), applied by `ui.c
    apply_preset_to` and `tests/hostsim.c host_preset_values`. Presets MOOG BASS, CRYPT PAD (LADR),
    BAT SCREAM, BLOOD ACID (MS20) are ANALOG 15..18.
  - `param_kept` moved from ui.c to params.c (the host harness applies baked extras too).
  - Tests: `tests/sound15_test.c` covers the new types (slopes, ringing at the cutoff via
    Goertzel, corners, SYNC/SUB, type switching). Listening files: `build/listen/filter_sweep_*.wav`,
    `build/listen/preset_*.wav`.
  - `tests/golden.txt` `preset/ANALOG/12_HARPSICHORD` changed 0714b6… → d5c32f92…: the owner
    baked a tweaked HARPSICHORD (reverb send 40 → 11) on 2026-10-17. It's in
    `baked_presets.json` and went into commit 8301871; the golden was only updated with step 2.
    **When a golden changes for a preset nobody touched in code, look at
    `firmware/src/baked_presets.json` first.** Baked replacements are expected golden changes.

## Sound design workflow (no hardware needed)

- `tests/hostsim.c` renders one engine/preset to WAV:
  `cc -O2 -w -Ibuild/gen -Ifirmware/src -o build/host/hostsim tests/hostsim.c -lm`, then
  `PRESET=1 NOTE=7 SECS=4 build/host/hostsim ENGINE PRESET 0 out.wav`. `NOTE=7` holds one C4;
  `CHORD=4` holds a chord; `SENDS=c,d,r` sets the sends; the last argument `0` = POLY.
- `PSET=id:value,...` overrides track params. **The ids in hostsim's comment are stale**: today
  P_E0 = 103 (E0..E7 = 103..110), P_ATK 1, P_DEC 2, P_SUS 3, P_REL 4, P_ED_FLT (env→filter) 5,
  P_REV 36, P_FTYPE 101, P_COUNT 111. `SWEEP=1` sweeps CUT and RES over the render.
- `BUDGET_UPDATE=1` rewrites every entry of `tests/target_budget.txt` with small unrelated drifts.
  To add new functions, add their lines by hand instead.
- Envelope times (`TIME_MS_X10`): 40 ≈ 18 ms, 50 ≈ 43 ms, 62 ≈ 67 ms, 76 ≈ 266 ms,
  88 ≈ 0.6 s, 98 ≈ 1.2 s, 104 ≈ 1.9 s, 112 ≈ 3.4 s.
- Judge renders with numbers (level, spectral centroid and RMS over time; numpy is installed),
  then give the owner WAVs to listen to: they decide by ear. Factory presets peak around −5 to −6 dBFS.
- Adding presets: append to the engine's `*_PRESETS[]` (indices are stored), and add one category
  letter per preset in `firmware/src/category.c` (a test checks the counts). Then update both
  goldens: `UPDATE=1 sh firmware/engines/tests/run.sh` (fork engines) and
  `GOLDEN_UPDATE=1 sh tests/run_tests.sh`. Check the diffs only add lines.

## Code map (where to look)

- `firmware/src/felucca.c` — the single translation unit; includes everything in order.
- `core.h` — tracks, params (`P_*`, `G_*`), steps. `engines.c` — engine table, PATTERNS, power-on parts (`TRK_DEF`).
- Engines: `eng_*.c` (src, GPL) and `engines/src/eng_*.c` (MIT); interface `engines/src/felucca_engine.h`.
  Presets: `preset_t {name, e[8], env{A,D,S,R}, fenv, mono, FX(dist,cho,dly,rev), PAT(n)}`.
- FM6 factory patches: `tools/gen_fm6_patches.py`. Preset categories: `category.c`.
- Mix/FX/reverb (ROOM, SPRING, HALL): `fx.c`; audio ISR: `audio.c`; voices/envelopes: `voice.c`.
- Sample storage/decoding: `eng_sample.c`, `eng_slice.c`, `slice_store.c`; flash: `storage.c`, `hal/fm1_flash.h`.
- UI: `ui*.c`, `gfx.c` (palette tokens), `tools/gen_ui_palettes.py`, `tools/gen_aa_font.py` (Inter Tight).
- Manual lives in the upstream wiki; editor protocol in `web/EDITOR_PROTOCOL.md`.

## Working conventions

- Code style: dense explanatory C comments that explain *why* and reference issues/versions;
  match it. Fixed-point, `static` everything, no libc beyond `libc.c`.
- Small, focused commits. Regenerate goldens only for intended sound changes and say so in the commit.
- Ask the owner before: new project/preset formats, forking the submodule, renaming the product,
  anything that changes stored layouts.
