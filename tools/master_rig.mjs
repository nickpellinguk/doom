// Headless BBC Master 128 rig for the textured port (docs/master_textured_spec.md).
//
//   node tools/master_rig.mjs display <disc.ssd> <expect.bin> <outdir>
//   node tools/master_rig.mjs engine  <disc.ssd> <addrs.json> <outdir>
//
// engine mode boots the Master engine disc and checks it RUNS: frames
// are counted by watching the driver's flip routine (addrs.flip_sched),
// the cursor keys turn (DV_ANGIDX) and walk (DV_PX*/DV_PY*), the HUD
// row is drawn, and only palette colours appear. Prints MASTERDISC:
// PASS or FAIL after the JSON line.
//
// Boots jsbeeb's Master 128 (MOS 3.20, DFS), *RUNs !BOOT from the disc,
// then MEASURES instead of eyeballing:
//   - shadow &3000-&7FFF byte-for-byte against <expect.bin> (20K)
//   - the program's frame counter (zp &74) advancing
//   - the displayed buffer alternating frame to frame
//   - the visible window's size in emulated pixels and that only the four
//     palette colours appear in it
// Writes frame_a.png / frame_b.png into <outdir> and prints one JSON line,
// then MASTERDISPLAY: PASS or FAIL.
//
// jsbeeb is found at $JSBEEB (default /home/user/jsbeeb): a clone of
// github.com/mattgodbolt/jsbeeb with `npm install` (plus `sharp`) done.
import fs from "fs";
import path from "path";

const JSBEEB = process.env.JSBEEB || "/home/user/jsbeeb";
const { MachineSession } = await import(path.join(JSBEEB, "src/machine-session.js"));

const [mode, disc, expectPath, outdir] = process.argv.slice(2);
if (mode === "engine") {
    await engineMode();
    process.exit(0);
}
if (mode !== "display") {
    console.error("usage: node tools/master_rig.mjs display <disc.ssd> <expect.bin> <outdir>");
    process.exit(2);
}
fs.mkdirSync(outdir, { recursive: true });

const PALETTE = { "0,0,0": "black", "255,0,0": "red", "0,255,255": "cyan", "255,255,255": "white" };
const W = 1024, H = 625;

const s = new MachineSession("Master");
await s.initialise();
await s.boot(30);
s.loadDisc(disc);
await s.type("*RUN !BOOT\r");
await s.runFrames(150);                     // load, MODE, draw both buffers, settle

const fails = [];
const out = {};

// 1. shadow RAM against the expected buffers
const expect = fs.readFileSync(expectPath);
const shadow = s.readMemory(0x3000, 0x5000, { shadow: true });
let bad = 0, firstBad = -1;
for (let i = 0; i < expect.length; i++) {
    if (shadow[i] !== expect[i]) { bad++; if (firstBad < 0) firstBad = i; }
}
out.shadow_mismatch = bad;
if (bad) fails.push(`shadow differs in ${bad} bytes, first at &${(0x3000 + firstBad).toString(16)}`);

// 2. frame counter advances
const frames = () => { const m = s.readMemory(0x74, 2); return m[0] | (m[1] << 8); };
const f0 = frames();
await s.runFrames(50);
const f1 = frames();
out.frames_counted = f1 - f0;
if (f1 - f0 < 45) fails.push(`frame counter moved ${f1 - f0} in 50 frames`);

// 3. two consecutive frames: one shows buffer A (vertical bands), the other B
function grab() {
    return new Uint8Array(s._completeFb8);   // RGBA, 1024 x 625
}
function px(fb, x, y) {
    const o = (y * W + x) * 4;
    return `${fb[o]},${fb[o + 1]},${fb[o + 2]}`;
}
async function frameAndShot(name) {
    await s.runFrames(1);
    const fb = grab();
    fs.writeFileSync(path.join(outdir, name), await s.screenshotActive({ scale: 1 }));
    return fb;
}
const fa = await frameAndShot("frame_1.png");
const fbb = await frameAndShot("frame_2.png");

function analyse(fb) {
    // bounding box of non-black pixels, colours used, and whether the
    // picture varies along x (A: vertical bands) or along y (B: horizontal)
    let minx = W, miny = H, maxx = -1, maxy = -1;
    const colours = new Set();
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const c = px(fb, x, y);
        if (c !== "0,0,0") {
            colours.add(c);
            if (x < minx) minx = x; if (x > maxx) maxx = x;
            if (y < miny) miny = y; if (y > maxy) maxy = y;
        }
    }
    const cy = (miny + maxy) >> 1, cx = (minx + maxx) >> 1;
    const alongX = new Set(), alongY = new Set();
    for (let x = minx; x <= maxx; x++) alongX.add(px(fb, x, cy) + "|" + px(fb, x, cy + 1));
    for (let y = miny; y <= maxy; y += 2) alongY.add(px(fb, cx, y) + "|" + px(fb, cx + 1, y));
    return { box: [minx, miny, maxx, maxy], colours: [...colours].map((c) => PALETTE[c] || c),
             kind: alongX.size > alongY.size ? "A" : "B" };
}
const A1 = analyse(fa), A2 = analyse(fbb);
out.frame_1 = A1; out.frame_2 = A2;
if (A1.kind === A2.kind) fails.push(`display not alternating (both frames show ${A1.kind})`);
for (const a of [A1, A2]) {
    const odd = a.colours.filter((c) => !Object.values(PALETTE).includes(c));
    if (odd.length) fails.push(`unexpected colours ${odd.join(" ")}`);
}
// union box: A gives the full height (its bands run top to bottom), B the
// full width (its bands run left to right); black bands make each partial.
const [a, b] = A1.kind === "A" ? [A1, A2] : [A2, A1];
const box = [b.box[0], a.box[1], b.box[2], a.box[3]];
out.window = { x: box[0], y: box[1], w: box[2] - box[0] + 1, h: box[3] - box[1] + 1 };
out.screen_centre_offset = { x: (box[0] + box[2]) / 2 - W / 2, y: (box[1] + box[3]) / 2 - H / 2 };
out.acccon = s.pagingState().acccon;

console.log(JSON.stringify(out));
for (const f of fails) console.log("FAIL: " + f);
console.log(fails.length ? "MASTERDISPLAY: FAIL" : "MASTERDISPLAY: PASS");
s.destroy();
process.exit(fails.length ? 1 : 0);

async function engineMode() {
    fs.mkdirSync(outdir, { recursive: true });
    const A = JSON.parse(fs.readFileSync(expectPath, "utf8"));
    const s = new MachineSession("Master");
    await s.initialise();
    await s.boot(30);
    s.loadDisc(disc);
    const fails = [], out = {}, out0 = out;
    let flips = 0;
    const cpu = s._machine.processor;
    cpu.debugInstruction.add((addr) => { if (addr === A.flip_sched) flips++; return false; });
    await s.type("*RUN !BOOT\r");
    // Loading ~76K through the emulated drive takes a while: run until the
    // driver has flipped a few frames (or give up after 6000 fields).
    for (let i = 0; i < 120 && flips < 5; i++) await s.runFrames(50);
    out0.boot_fields_waited = s._frameCount;
    const st = () => {
        const m = s.readMemory(A.DV_ANGIDX, 8);
        return { ang: m[0], px: m[3] | (m[4] << 8), py: m[6] | (m[7] << 8) };
    };
    out.flips_after_boot = flips;
    if (flips < 3) fails.push(`engine not flipping (${flips} flips after boot)`);
    const f0 = flips, s0 = st();
    await s.runFrames(400);                  // step 3's filler is slow: measure
    out.flips_per_400_fields = flips - f0;   // over 8 seconds of fields
    if (flips - f0 < 2) fails.push(`only ${flips - f0} frames in 400 fields`);
    const s1 = st();
    if (JSON.stringify(s0) !== JSON.stringify(s1)) fails.push("pose moved with no key held");
    s.keyDownRaw([9, 1]);                    // LEFT ($19): turn
    await s.runFrames(200);                  // held across several frames
    s.keyUpRaw([9, 1]);
    await s.runFrames(10);
    const s2 = st();
    out.turn = [s1.ang, s2.ang];
    if (s2.ang === s1.ang) fails.push("LEFT did not turn");
    s.keyDownRaw([9, 3]);                    // UP ($39): walk
    await s.runFrames(200);
    s.keyUpRaw([9, 3]);
    await s.runFrames(10);
    const s3 = st();
    out.walk = [[s2.px, s2.py], [s3.px, s3.py]];
    if (s3.px === s2.px && s3.py === s2.py) fails.push("UP did not move");
    fs.writeFileSync(path.join(outdir, "engine.png"), await s.screenshotActive({ scale: 1 }));
    // the control panel: both buffers' bottom 24 lines, exactly as loaded,
    // after the engine has drawn and flipped many frames over them
    const panel = new Uint8Array(fs.readFileSync(A.panel_bin));
    out.panel_bad = A.panel.map((a) => {
        const m = s.readMemory(a, panel.length, { shadow: true });
        let bad = 0;
        for (let i = 0; i < panel.length; i++) if (m[i] !== panel[i]) bad++;
        return bad;
    });
    if (out.panel_bad.some((b) => b)) fails.push(`control panel differs: ${out.panel_bad} bytes`);
    // the raster split (step 6c): over 50 fields, line 135 (the view's
    // last) keeps its Mode 2 colours out to the same pixel -- an early
    // switch would decode its right end as Mode 1 -- and line 136 (the
    // panel's first) shows none -- a late one would decode its left end as
    // Mode 2, or show Mode 1 pixels through the view's palette entries
    {
        const W = 1024, VIEW_ONLY = ["255,255,0", "0,255,0", "0,0,255", "255,0,255"];
        const rowM2 = (fb, y) => {
            let lo = -1, hi = -1;
            for (let x = 0; x < W; x++) {
                const o = (y * W + x) * 4;
                if (VIEW_ONLY.includes(`${fb[o]},${fb[o + 1]},${fb[o + 2]}`)) {
                    if (lo < 0) lo = x;
                    hi = x;
                }
            }
            return [lo, hi];
        };
        const TOP = 188;                       // line 0 in the frame buffer (2 rows a line)
        const y135 = TOP + 2 * 135, y136 = TOP + 2 * 136;
        // the panel below its black top line (lines 137..159) shows only its
        // four colours, red and cyan included: the IRQ rewrote entries 8..15,
        // which the view cycles (step 6e), in time
        const PANEL_OK = ["0,0,0", "255,255,255", "255,0,0", "0,255,255"];
        let ref = null, early = 0, late = 0, panelOdd = 0, panelRC = 0;
        const cyc = new Set();
        for (let f = 0; f < 50; f++) {
            await s.runFrames(1);
            const fb = new Uint8Array(s._completeFb8);
            const a = rowM2(fb, y135)[1], b = rowM2(fb, y136)[0];
            if (ref === null) ref = a;
            if (a !== ref) early++;
            if (b >= 0) late++;
            const seen = new Set();
            for (let y = 137; y < 160; y++) for (let x = 0; x < W; x++) {
                const o = ((TOP + 2 * y) * W + x) * 4;
                seen.add(`${fb[o]},${fb[o + 1]},${fb[o + 2]}`);
            }
            if ([...seen].some((c) => !PANEL_OK.includes(c))) panelOdd++;
            if (seen.has("255,0,0") && seen.has("0,255,255")) panelRC++;
            await s.runFor(10000);              // 5 ms into the field: the view's palette
            cyc.add(Array.from(s._machine.processor.video.actualPal.slice(8, 16)).join(","));
        }
        out.split = { line135_right: ref, early, late, panel_odd: panelOdd, panel_rc: panelRC,
                      cycle_states: cyc.size };
        if (panelOdd || panelRC < 50) fails.push(`panel colours wrong: ${panelOdd} fields odd, ${panelRC}/50 with red + cyan`);
        if (cyc.size < 3) fails.push(`colour cycle not running: ${cyc.size} palette states for 8..15 over 50 fields`);
        if (ref < 0) fails.push("split check: line 135 has no Mode 2 colours to test");
        if (early || late) fails.push(`raster split off: ${early} early, ${late} late of 50 fields`);
    }
    // screen content: palette only, and the HUD row lit
    const fb = new Uint8Array(s._completeFb8);
    const W = 1024, H = 625, cols = new Set();
    let minx = W, miny = H, maxx = -1, maxy = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const o = (y * W + x) * 4, c = `${fb[o]},${fb[o + 1]},${fb[o + 2]}`;
        if (c !== "0,0,0") { cols.add(c);
            minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y); }
    }
    // Mode 2 (step 6a): the eight solid colours (none of the flashing ones)
    const pal = ["255,0,0", "0,255,0", "255,255,0", "0,0,255", "255,0,255",
                 "0,255,255", "255,255,255"];
    const odd = [...cols].filter((c) => !pal.includes(c));
    if (odd.length) fails.push(`unexpected colours ${odd.join(" ")}`);
    out.lit_box = [minx, miny, maxx, maxy];
    out.flips_total = flips;
    out.acccon = s.pagingState().acccon;
    console.log(JSON.stringify(out));
    for (const f of fails) console.log("FAIL: " + f);
    console.log(fails.length ? "MASTERDISC: FAIL" : "MASTERDISC: PASS");
    s.destroy();
}
