// Tube transfer benchmark (tube-master step 2, docs/tube_master.md).
//
//   node tools/tube_bench.mjs [mhz]      (second processor clock, default 3)
//
// Boots jsbeeb's Master 128 with its 65C102 second processor, takes both
// CPUs over (interrupts off, the Tube's interrupt flags cleared) and has the
// second processor send 4,096 bytes through register 1 (its 24-byte FIFO,
// the only parasite -> host register with a real FIFO; it never interrupts
// the host) while the host reads them, polled. Each side's loop is run
// twice: as the plain measure, and with the parasite made slow so the
// FIFO is always full (host-bound), and with the parasite slowed.
// Prints one JSON line of host cycles per byte.
import path from "path";

const JSBEEB = process.env.JSBEEB || "/home/user/jsbeeb";
const { MachineSession } = await import(path.join(JSBEEB, "src/machine-session.js"));
const MHZ = Number(process.argv[2] || 3);
const N = 4096;

// host, at $1900: clear Q I J M V, drain R1, R2 <- go, then N bytes polled
// from R1 into $2000+ (BIT/BPL/LDA/STA abs,Y: the realistic consumer stores)
const host = (pad) => [
    0x78, //                1900 SEI
    0xa9, 0x1f, 0x8d, 0xe0, 0xfe, //  LDA #$1F : STA $FEE0
    0x2c, 0xe0, 0xfe, //    1906 f: BIT $FEE0
    0x10, 0x05, //               BPL go
    0xad, 0xe1, 0xfe, //         LDA $FEE1
    0x80, 0xf6, //               BRA f
    0xa9, 0x01, 0x8d, 0xe3, 0xfe, // 1910 go: LDA #1 : STA $FEE3
    0xa2, N >> 8, 0xa0, 0x00, //   LDX #pages : LDY #0
    0x2c, 0xe0, 0xfe, //    1919 lp: BIT $FEE0
    0x10, 0xfb, //               BPL lp
    0xad, 0xe1, 0xfe, //         LDA $FEE1
    0x99, 0x00, 0x20, //    1921 st: STA $2000,Y
    ...pad, //                   (NOPs: the slow-host variant)
    0xc8, //                     INY
    0xd0, (0xf2 - pad.length) & 0xff, // BNE lp
    0xee, 0x23, 0x19, //         INC st+2
    0xca, //                     DEX
    0xd0, (0xec - pad.length) & 0xff, // BNE lp
    0x4c, 0x00, 0x00, //         done: JMP done (patched)
];
// parasite, at $2000: wait for R2, then N bytes from $3000+ into R1, polled
const para = (pad) => [
    0x78, //                2000 SEI
    0x2c, 0xfa, 0xfe, //    2001 w: BIT $FEFA
    0x10, 0xfb, //               BPL w
    0xad, 0xfb, 0xfe, //         LDA $FEFB
    0xa2, N >> 8, 0xa0, 0x00, //   LDX #pages : LDY #0
    0x2c, 0xf8, 0xfe, //    200D lp: BIT $FEF8
    0x50, 0xfb, //               BVC lp
    0xb9, 0x00, 0x30, //    2012 ld: LDA $3000,Y
    0x8d, 0xf9, 0xfe, //         STA $FEF9
    ...pad,
    0xc8, //                     INY
    0xd0, (0xf2 - pad.length) & 0xff,
    0xee, 0x14, 0x20, //         INC ld+2
    0xca, //                     DEX
    0xd0, (0xec - pad.length) & 0xff,
    0x4c, 0x00, 0x00, //         done: JMP done (patched)
];
const nops = (n) => new Array(n).fill(0xea);

async function run(hostPad, paraPad) {
    const s = new MachineSession("Master", { tube: true });
    await s.initialise();
    await s.boot(30);
    const cpu = s._machine.processor;
    const tube = cpu.tube;
    tube.cpuMultiplier = MHZ / 4; // jsbeeb fits the 4MHz 65C102 to a Master
    const h = host(hostPad), p = para(paraPad);
    const hDone = 0x1900 + h.length - 3, pDone = 0x2000 + p.length - 3;
    h[h.length - 2] = hDone & 0xff;
    h[h.length - 1] = hDone >> 8;
    p[p.length - 2] = pDone & 0xff;
    p[p.length - 1] = pDone >> 8;
    h.forEach((b, i) => cpu.writemem(0x1900 + i, b));
    p.forEach((b, i) => (tube.memory[0x2000 + i] = b));
    for (let i = 0; i < N; i++) tube.memory[0x3000 + i] = (i * 7 + (i >> 8)) & 0xff;
    let t0 = null, t1 = null;
    const lp = 0x1919;
    cpu.debugInstruction.add((addr) => {
        if (addr === lp && t0 === null) t0 = cpu.cycleSeconds * 2000000 + cpu.currentCycles;
        if (addr === hDone && t1 === null) {
            t1 = cpu.cycleSeconds * 2000000 + cpu.currentCycles;
            return true;
        }
        return false;
    });
    tube.pc = 0x2000;
    cpu.pc = 0x1900;
    for (let i = 0; i < 200 && t1 === null; i++) await s.runFor(20000);
    let bad = 0;
    for (let i = 0; i < N; i++) if (cpu.readmem(0x2000 + i) !== ((i * 7 + (i >> 8)) & 0xff)) bad++;
    return { cycles_per_byte: t1 === null ? null : +((t1 - t0) / N).toFixed(2), bad };
}

const out = { parasite_mhz: MHZ, bytes: N };
// plain: the parasite's loop is the faster, so the FIFO stays full and the
// figure is the host's own cost per byte (its loop: BIT, BPL, LDA, STA
// abs,Y, INY, BNE)
out.plain = await run([], []);
// the parasite slowed by 40 NOPs a byte (80 of its cycles): the host now
// waits on it, which checks the measure against the parasite's clock
out.parasite_slowed = await run([], nops(40));
console.log(JSON.stringify(out));
