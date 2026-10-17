#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""oergelfm1: bake user presets into the firmware: they replace the factory preset they were made from.

The user presets come from a flash image: the browser emulator's saved sectors (web/emu, its page's localStorage:
{offset: base64 of 4096 bytes}), as tools/emu_server.py receives them from the page's BAKE button. A user preset saved by
this firmware knows the factory preset it was made from (upreset.c UP_ORIGIN_AT); baking it replaces that preset (its
name kept unless the sound was given one), or another factory preset of its engine, or adds it as a new one. Baked
presets are kept in firmware/src/baked_presets.json (the source, by parameter name: it survives renumbered P_* ids) and
generated into firmware/src/baked_presets.h: the FELUCCA_BAKED_<ENGINE> hook at the end of each engine's *_PRESETS[]
appends the added ones and overrides the replaced ones (designated initializers), and BAKED[] holds the values a preset_t
cannot hold (the LFOs, the matrix, the INSERT, the filter TYPE, the exact voice mode; engines.c baked_of, ui.c
apply_preset_to). A baked preset sounds as the user preset did, less what a factory load never touches (ui.c param_kept:
LEVEL PAN MUTE, the ARP, SCL, the SEQ settings, the SLICER, the chord keys: the track's own) and its 16-step pattern.
Removing a baked preset brings the factory one back.

  bake_presets.py list FLASH.json                   the user presets in a flash image (and what baking each does), the baked
  bake_presets.py bake FLASH.json SLOT[:TARGET][=NAME] ...
                                                    bake user preset slots (1..32): TARGET a factory preset index of the
                                                    engine, "new" to add it; default: the one it was made from
  bake_presets.py remove ENGINE/NAME ...            remove baked presets
  bake_presets.py header                            regenerate baked_presets.h from baked_presets.json
Not baked: FM6 sounds (their patch lives beside the record), DIGITAL's (retired). A SAMPLE / GRAIN / SLICE sound on a user
sample slot (USR1..3) keeps naming the slot: the sample is not baked.
After a bake: ./build.sh (and web/emu/build.sh); the regression's golden renders change for the replaced presets
(GOLDEN_UPDATE=1 sh tests/run_tests.sh). Formats: firmware/src/upreset.c (records), storage.c (commit records)."""
import base64
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "firmware" / "src"
STORE = SRC / "baked_presets.json"
HEADER = SRC / "baked_presets.h"
DESC = ROOT / "build" / "host" / "desc.json"

ST_MAGIC, ST_SECTOR, ST_PAYLOAD = 0x554C4546, 4096, 256                     # storage.c
OBJ_UPRESET0, UP_BANKS, UP_PER_BANK = 5, 2, 16                              # storage.c, upreset.c
UP_USED, UP_PMAX, UP_RSIZE, UP_BANK_MAGIC = 0xA5, 72, 192, 0x31425055
UP_CAT_AT = UP_PMAX * 2 - 1
UP_ORIGIN_AT = UP_CAT_AT - 1                   # upreset.c: the factory preset it was made from + 1
CATS = ["-", "BASS", "LEAD", "PAD", "PLUCK", "KEYS", "DRUM", "FX", "OTHER"]   # category.c CAT_*
NOT_BAKED = {1: "DIGITAL is retired", 12: "FM6 keeps its patch beside the record (not supported yet)"}
# preset_t holds these (ui.c apply_preset_to); P_VOICE only as POLY / LEGATO: the exact mode goes to BAKED[] too
IN_PRESET = ["P_ATK", "P_DEC", "P_SUS", "P_REL", "P_ED_FLT", "P_DIST", "P_CHOR", "P_DLY", "P_REV"]


def p_names():
    """core.h's track parameter enum: ["P_LEVEL", ...] in id order, up to P_COUNT"""
    s = (SRC / "core.h").read_text()
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    body = re.search(r"enum\s*\{([^}]*\bP_COUNT\b[^}]*)\}", s).group(1)
    names = [n.strip() for n in body.split(",") if n.strip()]
    return names[:names.index("P_COUNT")]


def kept(name):
    """ui.c param_kept: what a preset load leaves to the track (kept in step with it by hand)"""
    n = P_IDS
    i = n[name]
    return (name in ("P_LEVEL", "P_PAN", "P_MUTE", "P_CHRD", "P_VOIC", "P_SQNT") or n["P_AMODE"] <= i <= n["P_SGATE"]
            or n["P_SLCR"] <= i <= n["P_SLDEPTH"])


def desc():
    """the firmware's descriptors (tests/descdump.c: TP, the engines' EDIT and presets), built now"""
    DESC.parent.mkdir(parents=True, exist_ok=True)
    exe = DESC.parent / "descdump"
    subprocess.run(["cc", "-w", "-I" + str(ROOT / "build" / "gen"), "-I" + str(SRC), "-o", str(exe),
                    str(ROOT / "tests" / "descdump.c"), "-lm"], check=True, cwd=ROOT)
    DESC.write_text(subprocess.run([str(exe)], check=True, capture_output=True, text=True, cwd=ROOT).stdout)
    return json.loads(DESC.read_text())


P_NAMES = p_names()
P_IDS = {n: i for i, n in enumerate(P_NAMES)}


def crc32(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def flash_of(sectors):
    """{offset: base64 or bytes} -> a 1 MiB image (erased elsewhere)"""
    nor = bytearray(b"\xff" * 0x100000)
    for off, v in sectors.items():
        b = base64.b64decode(v) if isinstance(v, str) else bytes(v)
        off = int(off)
        if off % ST_SECTOR == 0 and len(b) == ST_SECTOR and off + ST_SECTOR <= len(nor):
            nor[off:off + ST_SECTOR] = b
    return nor


def st_current(nor, obj, sector):
    """storage.c st_current: the payload of the newest valid copy of obj (A at sector, B after it), or None"""
    best = None
    for copy in (0, 1):
        base = sector + copy * ST_SECTOR
        h = nor[base:base + 32]
        magic, typ, slot, seq, ln, crc, _, _, hcrc = struct.unpack("<IHHIIIIII", h)
        if magic != ST_MAGIC or typ != obj or slot != copy or ln > ST_SECTOR - ST_PAYLOAD or hcrc != crc32(h[:28]):
            continue
        body = bytes(nor[base + ST_PAYLOAD:base + ST_PAYLOAD + ln])
        if crc32(body) != crc:
            continue
        if best is None or (seq != best[0] and (seq - best[0]) & 0xFFFFFFFF < 0x80000000):
            best = (seq, body)
    return best and best[1]


def user_presets(nor, d):
    """the valid user presets: [{slot 1.., engine, name, cat, values[P_COUNT]}] (upreset.c up_values, by count)"""
    out = []
    for b in range(UP_BANKS):
        body = st_current(nor, OBJ_UPRESET0 + b, 0xDC000 + b * 2 * ST_SECTOR)
        if not body or len(body) < 8:
            continue
        magic, rsize, nslot = struct.unpack("<IHH", body[:8])
        if magic != UP_BANK_MAGIC or rsize != UP_RSIZE or nslot != UP_PER_BANK:
            continue
        for k in range(UP_PER_BANK):
            r = body[8 + k * UP_RSIZE:8 + (k + 1) * UP_RSIZE]
            if len(r) < UP_RSIZE:
                break
            used, ver, engine, np_ = r[0], r[1], r[2], r[3]
            name = r[4:16].split(b"\0")[0].decode("ascii", "replace")
            if used != UP_USED or not 1 <= ver <= 5 or engine >= len(d["ENG"]) or np_ < 8 or not name:
                continue
            raw = r[16:16 + UP_PMAX * 2]
            if ver >= 4:
                vals = [raw[i] - 64 for i in range(np_)]
                cat = raw[UP_CAT_AT] if np_ <= UP_CAT_AT else 0
            else:
                vals = list(struct.unpack(f"<{np_}h", raw[:np_ * 2]))
                cat = 0
            pc, pe0 = d["P_COUNT"], d["P_E0"]
            v = [t["def"] for t in d["TP"]][:pe0] + [x["def"] for x in d["ENG"][engine]["edit"]][:8]
            assert len(v) == pc                                # core.h params_by_count: the first np - 8, the last 8
            for i in range(min(np_ - 8, pe0)):
                v[i] = vals[i]
            for i in range(8):
                v[pe0 + i] = vals[np_ - 8 + i]
            if not 0 < cat < len(CATS):
                cat = 6 if engine in (10, 13) else 8           # category.c cat_of_engine
            origin = raw[UP_ORIGIN_AT] if ver >= 4 and np_ <= UP_ORIGIN_AT else 0
            out.append({"slot": b * UP_PER_BANK + k + 1, "engine": engine, "ename": d["ENG"][engine]["name"],
                        "name": name, "cat": CATS[cat], "values": v, "origin": origin})
    return out



def factory(d, store, ename):
    """engine ename's factory presets as they are without any bake: [(index, name)], aliases (SAMPLE's) left out"""
    e = next(x for x in d["ENG"] if x["name"] == ename)
    added = [p for p in store["presets"] if p["engine"] == ename and p.get("replaces") is None]
    names = [x["name"] for x in e["presets"]][:len(e["presets"]) - len(added)]
    for p in store["presets"]:
        if p["engine"] == ename and p.get("replaces") is not None and p["replaces"] < len(names):
            names[p["replaces"]] = p.get("factory_name", names[p["replaces"]])
    if ename == "SAMPLE":                              # SAMPLE 1 and 4 are aliases of PIANO (gen_samples.py SMP_SET_ORIG)
        return [(i, n) for i, n in enumerate(names) if n not in names[:i]]
    return list(enumerate(names))


def auto_named(u):
    """upreset.c up_auto_name: "<ENGINE> NN", a name nobody chose"""
    return re.fullmatch(re.escape(u["ename"][:8]) + r" \d\d", u["name"]) is not None


def proposal(u, d, store):
    """what baking u does unless told otherwise: replace the factory preset it was made from (its name kept unless the
    user named the sound), else add it; -> (target index or None, name)"""
    f = dict(factory(d, store, u["ename"])) if u["engine"] not in NOT_BAKED else {}
    t = u["origin"] - 1 if u["origin"] and u["origin"] - 1 in f else None
    return t, (f[t] if t is not None and auto_named(u) else u["name"])


def to_stored(u, d, target, name, factory_name, factory_pat=0):
    """a user preset -> its baked_presets.json entry: E1..E8 and preset_t's values, then what differs from TP's
    defaults (by name), the track's own (kept) left out; replaces: the factory preset's index, None: added"""
    v, pe0 = u["values"], d["P_E0"]
    params = {n: v[P_IDS[n]] for n in IN_PRESET}
    params["P_VOICE"] = v[P_IDS["P_VOICE"]]
    for i, n in enumerate(P_NAMES[:pe0]):
        if n not in params and not kept(n) and v[i] != d["TP"][i]["def"]:
            params[n] = v[i]
    e = {"engine": u["ename"], "name": name[:12], "cat": u["cat"], "replaces": target, "e": v[pe0:pe0 + 8], "params": params}
    if target is not None:
        e["factory_name"] = factory_name
        e["factory_pat"] = factory_pat                 # the pattern it suggests (SAVE > PHRASES), kept
    return e


def load_store():
    return json.loads(STORE.read_text()) if STORE.exists() else {"presets": []}


def save_store(s):
    STORE.write_text(json.dumps(s, indent=1) + "\n")


def cstr(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def header(store, d):
    """baked_presets.h from the store"""
    eng = [e["name"] for e in d["ENG"]]
    L = ["/* SPDX-License-Identifier: GPL-3.0-only */",
         "/* generated by tools/bake_presets.py from baked_presets.json (oergelfm1): do not edit, re-bake or edit the json",
         " * and run `python3 tools/bake_presets.py header`. Presets baked from the emulator: FELUCCA_BAKED_<ENGINE>, at the",
         " * end of the engine's *_PRESETS[], first appends the added ones, then replaces factory presets by designated",
         " * initializers ([k] = {...}: C99, the last initializer of an element wins). BAKED[] holds the values a preset_t",
         " * cannot, per baked preset (index 255: the added ones, in order) (engines.c baked_of, ui.c apply_preset_to).",
         " * Included by engines.c */",
         "#ifdef __clang__",
         "#pragma clang diagnostic ignored \"-Winitializer-overrides\"   /* the replacements, on purpose */",
         "#endif",
         "#define BAKED_ADDED 255u",
         "typedef struct { uint8_t id; int8_t v; } baked_kv_t;           /* a track parameter P_* and its value */",
         "typedef struct { uint8_t engine, index, cat, nkv; const baked_kv_t *kv; } baked_t;   /* index: BAKED_ADDED or the",
         "                                                                                      * factory preset replaced */",
         ""]
    rows, k, nadd = [], 0, [0] * 16
    for i, n in enumerate(eng):
        if n == "-":
            continue
        ps = [p for p in store["presets"] if p["engine"] == n]
        ps = [p for p in ps if p.get("replaces") is None] + sorted((p for p in ps if p.get("replaces") is not None),
                                                                  key=lambda p: p["replaces"])
        macro = re.sub(r"\W", "_", n)
        nadd[i] = sum(1 for p in ps if p.get("replaces") is None)
        L.append(f"#define BAKED_N_{macro} {nadd[i]}u   /* added */")
        if not ps:
            continue
        lines = []
        for p in ps:
            q = p["params"]
            env = ", ".join(str(q[x]) for x in ("P_ATK", "P_DEC", "P_SUS", "P_REL"))
            fx = ", ".join(str(q[x]) for x in ("P_DIST", "P_CHOR", "P_DLY", "P_REV"))
            mono = int(q.get("P_VOICE", 0) != 0)
            at = "" if p.get("replaces") is None else f"[{p['replaces']}] = "
            pat = f", PAT({p['factory_pat']})" if p.get("factory_pat") else ""
            lines.append(f'    {at}{{{cstr(p["name"])}, {{{", ".join(map(str, p["e"]))}}}, {{{env}}}, {q["P_ED_FLT"]}, {mono}, '
                         f'FX({fx}){pat}}},')
            kv = [(P_IDS[x], val) for x, val in q.items() if x in P_IDS and x not in IN_PRESET and
                  not (x == "P_VOICE" and val == 0)]
            idx = "BAKED_ADDED" if p.get("replaces") is None else str(p["replaces"])
            what = "added" if p.get("replaces") is None else f"replaces {p.get('factory_name', '?')}"
            rows.append((i, idx, CATS.index(p["cat"]) if p["cat"] in CATS else 8, len(kv), k,
                         ", ".join(f"{{{a}, {b}}}" for a, b in sorted(kv)), f"{n} {p['name']} ({what})"))
            k += 1
        L.append(f"#define FELUCCA_BAKED_{macro} \\")
        L.append(" \\\n".join(lines))
    L.append("")
    L.append(f"#define BAKED_COUNT {len(rows)}u")
    for i, idx, c, n, k, kv, what in rows:
        if n:
            L.append(f"static const baked_kv_t BAKED_KV{k}[] = {{{kv}}};   /* {what} */")
    L.append("static const baked_t BAKED[BAKED_COUNT ? BAKED_COUNT : 1u] = {")
    for i, idx, c, n, k, _, what in rows:
        L.append(f"    {{{i}, {idx}, {c}, {n}, {'BAKED_KV%d' % k if n else '0'}}},   /* {what} */")
    if not rows:
        L.append("    {0, 0, 0, 0, 0},")
    L.append("};")
    L.append("static const uint8_t BAKED_N[16] = {" + ", ".join(map(str, nadd)) + "};   /* added, per engine */")
    HEADER.write_text("\n".join(L) + "\n")


def bake(sectors, items, d=None):
    """bake user presets from a flash image into the store and the header -> (what was baked, notes).
    items: [{"slot": 1.., "target": "auto" (the proposal) | "new" | factory index, "name": optional}]"""
    d = d or desc()
    us = {u["slot"]: u for u in user_presets(flash_of(sectors), d)}
    store, done, notes = load_store(), [], []
    for it in items:
        u = us.get(int(it["slot"]))
        if not u:
            notes.append(f"slot {it['slot']}: empty")
            continue
        if u["engine"] in NOT_BAKED:
            notes.append(f"{u['name']} ({u['ename']}): not baked, {NOT_BAKED[u['engine']]}")
            continue
        if u["ename"] in ("SAMPLE", "GRAIN") and u["values"][d["P_E0"]] >= 5 or \
                u["ename"] == "SLICE" and u["values"][d["P_E0"]] in (1, 2, 3):
            notes.append(f"{u['name']}: plays a user sample slot; the sample itself is not baked")
        f = dict(factory(d, store, u["ename"]))
        t, name = proposal(u, d, store)
        tg = it.get("target", "auto")
        if tg == "new":
            t, name = None, u["name"]
        elif tg != "auto":
            t = int(tg)
            if t not in f:
                notes.append(f"{u['name']}: {u['ename']} has no factory preset {t}")
                continue
            name = f[t] if auto_named(u) else u["name"]
        name = (it.get("name") or name).strip().upper()[:12] or name
        pat = 0
        if t is not None:                               # the factory preset's pattern hint: as stored if it was replaced
            prev = next((p for p in store["presets"] if p["engine"] == u["ename"] and p.get("replaces") == t), None)
            pat = prev.get("factory_pat", 0) if prev else d["ENG"][u["engine"]]["presets"][t].get("pat", 0)
        e = to_stored(u, d, t, name, f.get(t), pat)
        store["presets"] = [p for p in store["presets"] if p["engine"] != e["engine"] or
                            (p.get("replaces") != t if t is not None else p.get("replaces") is not None or p["name"] != name)]
        store["presets"].append(e)
        done.append(f"{e['engine']}/{name}" + (f" (replaces {f[t]})" if t is not None else " (added)"))
    save_store(store)
    header(store, d)
    return done, notes


def remove(keys, d=None):
    """remove baked presets: "ENGINE/NAME" (or NAME); a replaced factory preset comes back"""
    d = d or desc()
    store = load_store()
    before = len(store["presets"])
    store["presets"] = [p for p in store["presets"] if p["name"] not in keys and f"{p['engine']}/{p['name']}" not in keys]
    save_store(store)
    header(store, d)
    return before - len(store["presets"])


def listing(sectors, d=None):
    d = d or desc()
    store, user, fac = load_store(), [], {}
    for u in user_presets(flash_of(sectors), d):
        ok = u["engine"] not in NOT_BAKED
        t, name = proposal(u, d, store) if ok else (None, u["name"])
        if ok and u["ename"] not in fac:
            fac[u["ename"]] = [{"index": i, "name": n} for i, n in factory(d, store, u["ename"])]
        user.append({k: u[k] for k in ("slot", "ename", "name", "cat")} | {"bakeable": ok, "target": t, "bake_name": name})
    return {"user": user, "factory": fac,
            "baked": [{"engine": p["engine"], "name": p["name"], "cat": p["cat"], "replaces": p.get("factory_name")}
                      for p in store["presets"]]}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "header":
        header(load_store(), desc())
        print(f"wrote {HEADER.relative_to(ROOT)}")
    elif cmd == "list":
        r = listing(json.loads(Path(argv[2]).read_text()))
        for u in r["user"]:
            to = "not bakeable" if not u["bakeable"] else (f"replaces {next(f['name'] for f in r['factory'][u['ename']] if f['index'] == u['target'])}"
                                                           if u["target"] is not None else "added")
            print(f"  U{u['slot']:02d} {u['ename']:7s} {u['name']:12s} {u['cat']:6s} -> {u['bake_name']} ({to})")
        print("baked:", ", ".join(f"{p['engine']}/{p['name']}" + (f" (replaces {p['replaces']})" if p["replaces"] else "")
                                  for p in r["baked"]) or "none")
    elif cmd == "bake":
        items = []
        for a in argv[3:]:                              # SLOT[:TARGET][=NAME], TARGET a factory index or "new"
            a, _, name = a.partition("=")
            slot, _, tg = a.partition(":")
            items.append({"slot": int(slot), "target": tg or "auto", "name": name})
        done, notes = bake(json.loads(Path(argv[2]).read_text()), items)
        print("baked:", ", ".join(done) or "nothing")
        for n in notes:
            print("  note:", n)
    elif cmd == "remove":
        print(f"removed {remove(argv[2:])}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
