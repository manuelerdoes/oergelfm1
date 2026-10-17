// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
// oergelfm1: the BAKE path end to end, in two runs around a bake (tests/bake_e2e.sh):
//   node web/emu/bake_test.mjs save WASM FLASH.json   ANALOG's ACID on track 1, edited on the panel (CUT, RES on HOME;
//                                                     the LFO page's knobs: values a preset_t cannot hold), saved as
//                                                     user preset 1 (SAVE, OCT+, OCT+); the flash as the page keeps it,
//                                                     and factory ACID's phrase (FLASH.json.acid) to tell it apart later
//   node web/emu/bake_test.mjs check WASM FLASH.json  after the bake (it replaces ACID, the preset it was made from) and
//                                                     the rebuild: user preset 1 and ACID's place (MIDI program change 4)
//                                                     play the same phrase, bit for bit, and not factory ACID's
import fs from "fs";
import { storeSectors, restoreSectors } from "./worklet.js";

const [mode, wasmPath, flashPath] = process.argv.slice(2);
const bytes = fs.readFileSync(wasmPath);
const B = { LFO: 3, HOME: 6, SAVE: 7, OCTDN: 12, OCTUP: 13 };
const EN = { PRESETS: 2, K1: 3, K2: 4, K3: 5, K4: 6 };
const ACID = 4;                                       // ANALOG's ACID (engines.c TRK_DEF: track 1 at power-on)

async function device(saved) {
  const { instance } = await WebAssembly.instantiate(bytes, {});
  const ex = instance.exports, mem = ex.memory;
  ex._initialize();
  ex.web_nor_erase();
  if (saved) restoreSectors(new Uint8Array(mem.buffer, ex.web_nor(), ex.web_nor_size()), saved);
  ex.web_boot();
  const d = { ex, mem, kept: null };
  d.render = (ms) => {
    const n = Math.round(ms * 44.1);
    for (let k = 0; k < n; k += 128) {
      const m = Math.min(128, n - k);
      ex.web_render(m);
      if (d.kept) {
        const l = new Float32Array(mem.buffer, ex.web_out_l(), m), r = new Float32Array(mem.buffer, ex.web_out_r(), m);
        for (let i = 0; i < m; i++) d.kept.push(l[i], r[i]);
      }
    }
  };
  d.tap = (b) => { ex.web_buttons(1 << b); d.render(60); ex.web_buttons(0); d.render(60); };
  d.turn = (role, n) => { for (let i = 0; i < Math.abs(n); i++) { ex.web_enc(role, Math.sign(n)); d.render(40); } };
  d.pc = (n) => { ex.web_midi(0xC0, n, 0); d.render(100); };   // program change, channel 1: track 1
  d.phrase = () => {                                   // C4, then E4 + G4, released: the sound and its tail
    d.kept = [];
    ex.web_keys(1 << 7); d.render(400); ex.web_keys(0); d.render(200);
    ex.web_keys(1 << 11 | 1 << 14); d.render(400); ex.web_keys(0); d.render(1200);
    const s = Float32Array.from(d.kept); d.kept = null; return s;
  };
  return d;
}
// a fresh device on the flash, a sound loaded, its phrase; every load takes the same time (a free LFO's phase)
const run = async (sectors, load) => { const d = await device(sectors); d.render(1500); load(d); d.render(300); return d.phrase(); };
const factoryAcid = (d) => { d.pc(ACID); d.render(40); };

if (mode === "save") {
  const d = await device(null);
  d.render(1500);                                     // the splash; track 1 is ANALOG ACID
  d.turn(EN.K1, 9);                                   // HOME: CUT up
  d.turn(EN.K2, -12);                                 // RES down
  d.tap(B.LFO);                                       // the LFO page: its knobs are no preset_t's
  d.turn(EN.K1, 6); d.turn(EN.K2, 2); d.turn(EN.K3, 25);
  d.tap(B.HOME);
  d.tap(B.SAVE); d.tap(B.OCTUP); d.tap(B.OCTUP);      // SAVE > USER preset 1 (web/emu/emu_test.mjs)
  d.render(500);
  const sectors = storeSectors(new Uint8Array(d.mem.buffer, d.ex.web_nor(), d.ex.web_nor_size()));
  const out = {};
  for (const [k, v] of Object.entries(sectors)) out[k] = Buffer.from(v).toString("base64");
  fs.writeFileSync(flashPath, JSON.stringify(out));
  fs.writeFileSync(flashPath + ".acid", Buffer.from((await run(sectors, factoryAcid)).buffer));
  console.log(`bake_test: saved ${Object.keys(out).length} sectors to ${flashPath}, factory ACID's phrase beside it`);
} else if (mode === "check") {
  const raw = JSON.parse(fs.readFileSync(flashPath, "utf8")), sectors = {};
  for (const [k, v] of Object.entries(raw)) sectors[k] = new Uint8Array(Buffer.from(v, "base64"));
  const buf = fs.readFileSync(flashPath + ".acid"), before = new Float32Array(buf.buffer, buf.byteOffset, buf.length / 4);
  // user preset 1: ANALOG's first preset (PC 0), then PRESETS one left: the list wraps to its end, the user presets
  const user = await run(sectors, (d) => { d.pc(0); d.turn(EN.PRESETS, -1); });
  const baked = await run(sectors, factoryAcid);
  const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  const peak = (a) => a.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  let dmax = 0;
  for (let i = 0; i < Math.min(user.length, baked.length); i++) dmax = Math.max(dmax, Math.abs(user[i] - baked[i]));
  console.log(`bake_test: user preset peak ${peak(user).toFixed(3)}, ACID's place now peak ${peak(baked).toFixed(3)}, ` +
              `largest difference ${dmax.toExponential(2)}; factory ACID before peak ${peak(before).toFixed(3)}`);
  const ok = peak(baked) > 0.01 && same(user, baked) && !same(baked, before);
  console.log(`bake_test: ACID's place plays the user preset's sound bit for bit, no longer factory ACID ${ok ? "ok" : "FAIL"}`);
  process.exit(ok ? 0 : 1);
}
