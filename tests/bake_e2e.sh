#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
# oergelfm1: the emulator's BAKE path end to end (tools/bake_presets.py, web/emu/bake_test.mjs). Rebuilds the firmware
# and the emulator twice, so it is not part of tests/run_tests.sh; run it after changing the bake code:
#   tests/bake_e2e.sh
# A user preset is made from ANALOG's ACID on the emulator's panel and saved, then baked: it replaces ACID (the
# preset count stays) and ACID's place must play the user preset's sound bit for bit. baked_presets.json / .h are put back as they were afterwards.
set -e
cd "$(dirname "$0")/.."
W=build/bake_e2e
mkdir -p "$W"
cp firmware/src/baked_presets.h "$W/h.orig"
[ -f firmware/src/baked_presets.json ] && cp firmware/src/baked_presets.json "$W/json.orig" || rm -f "$W/json.orig"
restore() {
    cp "$W/h.orig" firmware/src/baked_presets.h
    if [ -f "$W/json.orig" ]; then cp "$W/json.orig" firmware/src/baked_presets.json; else rm -f firmware/src/baked_presets.json; fi
}
trap restore EXIT
./build.sh >/dev/null && web/emu/build.sh >/dev/null
node web/emu/bake_test.mjs save build/emu/felucca.wasm "$W/flash.json"
python3 tools/bake_presets.py list "$W/flash.json" | tee "$W/list.txt"
grep -q "(replaces ACID)" "$W/list.txt" || { echo "bake_e2e: user preset 1 does not know it was made from ACID FAIL"; exit 1; }
N0=$(python3 -c "import sys; sys.path.insert(0, 'tools'); import bake_presets as b; print(len(b.desc()['ENG'][0]['presets']))")
python3 tools/bake_presets.py bake "$W/flash.json" 1
./build.sh >/dev/null && web/emu/build.sh >/dev/null
N1=$(python3 -c "import sys; sys.path.insert(0, 'tools'); import bake_presets as b; print(len(b.desc()['ENG'][0]['presets']))")
[ "$N0" = "$N1" ] || { echo "bake_e2e: ANALOG has $N1 presets after the bake, $N0 before FAIL"; exit 1; }
node web/emu/bake_test.mjs check build/emu/felucca.wasm "$W/flash.json"
restore
trap - EXIT
./build.sh >/dev/null && web/emu/build.sh >/dev/null
echo "bake_e2e: ok (firmware and emulator rebuilt without the test's preset)"
