"""The music gates' py65 memory (System VIA port A, its latch and ROMSEL
watched) and a JSR caller: shared by test_sfx.py (H9)."""
import abi


class Mem(list):
    """64K with System VIA port A, its latch and ROMSEL watched."""
    def __init__(self):
        super().__init__([0] * 0x10000)
        self.ddra, self.ora, self.latch = 0x7F, 0, [1] * 8
        self.chip, self.bad = [], []

    def __getitem__(self, a):
        if a == 0xFE43:
            return self.ddra
        if a == 0xFE4F:
            return self.ora & self.ddra     # (PA7 in: reads 0 here)
        return list.__getitem__(self, a)

    def __setitem__(self, a, v):
        if a == 0xFE43:
            self.ddra = v
        elif a == 0xFE4F:
            self.ora = v
        elif a == 0xFE40:
            bit, n = v >> 3 & 1, v & 7
            if n == 0 and bit == 0 and self.latch[0]:
                if self.ddra != 0xFF or not self.latch[3]:
                    self.bad.append(f'chip write with DDRA ${self.ddra:02X}, latch 3 = {self.latch[3]}')
                self.chip.append(self.ora)
            self.latch[n] = bit
        elif a == 0xFE30:
            if v != abi.BANK_WALK:
                self.bad.append(f'ROMSEL {v}')
        elif 0xFC00 <= a < 0xFF00:
            self.bad.append(f'write ${a:04X}')
        else:
            list.__setitem__(self, a, v)


def caller(mem, mpu, tramp=0x0200):
    list.__setitem__(mem, tramp, 0x20)          # JSR target, then stop at +3

    def call(addr, keep_a=False):
        list.__setitem__(mem, tramp + 1, addr & 0xFF)
        list.__setitem__(mem, tramp + 2, addr >> 8)
        mpu.pc, mpu.sp = tramp, 0xFF
        c0 = mpu.processorCycles
        while mpu.pc != tramp + 3:
            mpu.step()
            if mpu.processorCycles - c0 > 200000:
                raise SystemExit(f'runaway at ${mpu.pc:04X}')
        return mpu.processorCycles - c0
    return call
