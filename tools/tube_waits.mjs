// Where each side of the Tube pipeline waits (docs/tube_master.md H4d).
//
//   node tools/tube_waits.mjs labels.json disc.ssd mhz out.json
//
// (tools/tube_waits.py makes labels.json, runs this, prints the table.)
// Boots the Tube disc on jsbeeb's Master with its second processor at
// mhz, walks a fixed key plan from the spawn (1,500 fields) and classes
// every cycle of both processors while it does:
//   host: render_frame (the engine, sending), within it the PUTB polls
//     that loop (waiting to send); hd_frame (drawing), within it the
//     GETB polls that loop (waiting for list bytes); flip_sched; the rest
//   second processor: fs_irq (taking request bytes), fs_poll called from
//     the fill (sending the list), fs_get's empty ring (waiting for
//     requests), fs_main's wait for the last list to be out (waiting for
//     the host to take it), and the rest (the fill)
import path from "path"; import fs from "fs";
const { MachineSession } = await import(path.join("/home/user/jsbeeb", "src/machine-session.js"));
const [labPath, disc, mhzS, outPath] = process.argv.slice(2);
const L = JSON.parse(fs.readFileSync(labPath)); const mhz = +mhzS;
const s = new MachineSession("Master", { tube: true });
await s.initialise();
const cpu = s._machine.processor, tube = cpu.tube;
tube.cpuMultiplier = mhz / 4;
await s.boot(30);
s.loadDisc(disc);
const clock = () => cpu.cycleSeconds * 2000000 + cpu.currentCycles;
let on = false;
// host
const H = { render: 0, sendwait: 0, hd: 0, listwait: 0, flip: 0 }; let flips = 0;
const spans = []; let lastBit = -1, lastBitT = 0, between = 0;
const isBit = (a) => cpu.readmem(a) === 0x2c && cpu.readmem(a + 1) === 0xe0 && cpu.readmem(a + 2) === 0xfe;
cpu.debugInstruction.add((a) => {
    const t = clock();
    if (a === L.flip_sched) flips++;
    if (spans.length && a === spans[spans.length - 1].ret) {
        const sp = spans.pop(); if (on) H[sp.name] += t - sp.t0;
    }
    const nm = a === L.render_frame ? "render" : a === L.hd_frame ? "hd" : a === L.flip_sched ? "flip" : null;
    if (nm) {
        const sp = cpu.s; const r = (cpu.readmem(0x101 + sp) | (cpu.readmem(0x102 + sp) << 8)) + 1;
        spans.push({ name: nm, ret: r, t0: t });
    }
    if (isBit(a)) {
        if (a === lastBit && between === 1 && on && spans.length) {
            const top = spans[spans.length - 1].name;
            H[top === "hd" ? "listwait" : "sendwait"] += t - lastBitT;
        }
        lastBit = a; lastBitT = t; between = 0;
    } else between++;
    return false;
});
// second processor
const P = { work: 0, getwait: 0, listwait: 0, irq: 0, send: 0, bytes: 0 }; let mode = "work";
tube.execute = function (cycles) {
    this.cycles += cycles * this.cyclesPerHostCycle * this.cpuMultiplier;
    if (this.cycles < 3) return;
    while (this.cycles > 0) {
        const pc = this.pc;
        if (pc === L.fs_wait) mode = "listwait";
        else if (pc === L.fs_frame) mode = "work";
        else if (pc === L.fs_getcli) mode = "getwait";
        else if (pc === L.fs_getg && mode === "getwait") mode = "work";
        const c0 = this.cycles;
        const opcode = this.readmem(this.pc);
        this.incpc();
        this.runner.run(opcode);
        if (this.takeInt) this.brk(true);
        if (on) {
            const d = c0 - this.cycles;
            const inPoll = pc >= L.fs_poll && pc < L.fs_frame;
            P[pc >= L.fs_irq && pc < L.fs_irq_end ? "irq" : inPoll && mode === "work" ? "send" : mode] += d;
            if (inPoll && opcode === 0x8d) P.bytes++;
        }
    }
};
await s.type("*RUN !BOOT\r");
for (let i = 0; i < 120 && flips < 5; i++) await s.runFrames(50);
await s.runFrames(100);
const KEYS = { up: [9, 3], left: [9, 1], right: [9, 7] };
const plan = [["up", 300], ["left", 100], ["up", 300], ["right", 150], ["up", 300], ["left", 100], ["up", 250]];
on = true; const f0 = flips, t0 = clock();
for (const [k, n] of plan) { s.keyDownRaw(KEYS[k]); await s.runFrames(n); s.keyUpRaw(KEYS[k]); }
on = false;
const frames = flips - f0, T = clock() - t0;
// para: the second processor's own cycles (mhz MHz)
const out = { mhz, frames, fields: plan.reduce((a, b) => a + b[1], 0), host_total: T, host: H, para: P };
fs.writeFileSync(outPath, JSON.stringify(out));
console.log(JSON.stringify(out));
