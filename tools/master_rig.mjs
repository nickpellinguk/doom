// Headless BBC Master 128 rig for the textured port (docs/master_textured_spec.md).
//
//   node tools/master_rig.mjs display <disc.ssd> <expect.bin> <outdir>
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
if (mode !== "display") {
    console.error("usage: node tools/master_rig.mjs display <disc.ssd> <expect.bin> <outdir>");
    process.exit(2);
}
fs.mkdirSync(outdir, { recursive: true });

const PALETTE = { "0,0,0": "black", "255,0,255": "magenta", "0,255,255": "cyan", "255,255,255": "white" };
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
