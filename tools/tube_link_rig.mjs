// The Tube Master link on jsbeeb (docs/tube_master.md H4a).
//
//   node tools/tube_link_rig.mjs in.json out.json
//
// Boots jsbeeb's Master 128 with its 65C102 second processor (clocked to
// in.mhz), puts the fill server's image (tube_server.py) in the second
// processor's memory and starts it at fs_main. The host runs a pump, as
// the real host will, pipelined: frame N's requests out through register 1
// (the server takes them by IRQ), then frame N - 1's display list in
// through register 1, polled. in.json: {mhz, image: {load, hex, entry},
// frames: [request hex], lists: [length of each list, frame -1's first],
// pump: {hex, pump_n, done}}.
// out.json: the lists' hex, and each frame's host cycles.
import fs from "fs";
import path from "path";

const JSBEEB = process.env.JSBEEB || "/home/user/jsbeeb";
const { MachineSession } = await import(path.join(JSBEEB, "src/machine-session.js"));
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const hex = (h) => Uint8Array.from(Buffer.from(h, "hex"));

const PUMP = 0x1900; //    the host pump (lpump.s's link address)
const REQ = 0x3000; //     a frame's requests (<= 2.6K)
const LIST = 0x4000; //    the list read back (<= 5.4K)
const ZP = 0x70; //        $70 count out, $72 ptr out, $74 count in, $76 ptr in
// the pump: src/tube/lpump.s, assembled by test_tube_link.py at PUMP
const pump = hex(cfg.pump.hex);
const DONE = cfg.pump.done;
const ENTRY_NOFLAGS = cfg.pump.pump_n; // later frames: the flags are set

const s = new MachineSession("Master", { tube: true });
await s.initialise();
await s.boot(30);
const cpu = s._machine.processor;
const tube = cpu.tube;
tube.cpuMultiplier = cfg.mhz / 4; // jsbeeb fits the 4MHz 65C102 to a Master
const img = hex(cfg.image.hex);
img.forEach((b, i) => (tube.memory[cfg.image.load + i] = b));
tube.pc = cfg.image.entry;
pump.forEach((b, i) => cpu.writemem(PUMP + i, b));
const clock = () => cpu.cycleSeconds * 2000000 + cpu.currentCycles;

let done = false;
cpu.debugInstruction.add((addr) => {
    if (addr === DONE) {
        done = true;
        return true;
    }
    return false;
});
const w16 = (a, v) => {
    cpu.writemem(a, v & 0xff);
    cpu.writemem(a + 1, v >> 8);
};
// let the server start (it sends frame -1's list, one byte, when ready)
await s.runFor(200000);
const out = { lists: [], cycles: [] };
const frames = [...cfg.frames, null]; // a last pass for the last list
for (let n = 0; n < frames.length; n++) {
    const req = frames[n] ? hex(frames[n]) : new Uint8Array(0);
    req.forEach((b, i) => cpu.writemem(REQ + i, b));
    w16(ZP, req.length);
    w16(ZP + 2, REQ);
    w16(ZP + 4, cfg.lists[n]);
    w16(ZP + 6, LIST);
    cpu.pc = n === 0 ? PUMP : ENTRY_NOFLAGS;
    done = false;
    const t0 = clock();
    for (let i = 0; i < 4000 && !done; i++) await s.runFor(20000);
    if (!done) {
        const u = tube.tube;
        const h = (v) => v.toString(16);
        out.error =
            `frame ${n}: the pump never finished: host pc $${h(cpu.pc)} out left ${cpu.readmem(ZP) | (cpu.readmem(ZP + 1) << 8)}` +
            ` in left ${cpu.readmem(ZP + 4) | (cpu.readmem(ZP + 5) << 8)}; server pc $${h(tube.pc)}` +
            ` flags $${h(u.internalStatusRegister)}`;
    }
    out.cycles.push(clock() - t0);
    const l = [];
    for (let i = 0; i < cfg.lists[n]; i++) l.push(cpu.readmem(LIST + i));
    out.lists.push(Buffer.from(l).toString("hex"));
    if (out.error) break;
}
fs.writeFileSync(process.argv[3], JSON.stringify(out));
