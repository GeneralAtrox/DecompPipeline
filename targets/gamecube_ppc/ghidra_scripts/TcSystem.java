// GameCube machine state the oracle models beside the registers a function owns (TcFixups and TcCallOther call
// these; the translator and the C runtime follow the same rules):
//  - dcbi: no cache is modelled (memory is the line), so dcbi is a no-op, unless the run wrote into its 32-byte block
//    before it: then the hardware may drop that write, and the vector is invalid (TcOracle drops it,
//    reason in `invalid`). DCInvalidateRange's callers flush or never write the block first.
//  - mtmsr: the SLEIGH keeps only MSR's EE and ME bits (MSR = MSR & 0x9000 | ...); the 750CL writes the whole register.
//    The fixup writes rS to MSR. MSR starts at MSR_ENTRY (the game's running value: EE ME FP IR DR RI; BS2 boots with
//    0x2032 and OSInit enables EE and ME), on both sides, and its exit value is compared when it differs (TcVectors
//    "MSR"). mfmsr is MSR as is.
//  - sc: the SLEIGH calls a syscall() CALLOTHER. The GameCube OS's system-call handler (0x80000c00, copied there by
//    OSInit) is mfspr r9,HID0; ori r10,r9,8; mtspr HID0,r10; isync; sync; mtspr HID0,r9; rfi: its only effects a
//    program sees are r9 = HID0 and r10 = HID0 | 8. The callback writes those.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.AddressSpace;
import java.math.BigInteger;

public class TcSystem {
    // MSR at entry, on both sides (translate.MSR_ENTRY, tc_rt.h TC_MSR_ENTRY): EE ME FP IR DR RI.
    static final long MSR_ENTRY = 0xB032L;

    // The language's register named one of names (the SLEIGH's spelling), or the first name.
    static String name(EmulatorHelper emu, String... names) {
        for (String n : names) {
            if (emu.getLanguage().getRegister(n) != null) return n;
            if (emu.getLanguage().getRegister(n.toLowerCase()) != null) return n.toLowerCase();
        }
        return names[0];
    }

    static String msr(EmulatorHelper emu) {
        return name(emu, "MSR");
    }

    // sc: what the OS's system-call handler leaves (see the header).
    static void sc(EmulatorHelper emu) {
        long h = emu.readRegister(name(emu, "HID0", "spr3f0")).longValue() & 0xffffffffL;
        emu.writeRegister("r9", BigInteger.valueOf(h));
        emu.writeRegister("r10", BigInteger.valueOf(h | 8));
    }

    // dcbi of the block holding ea, after the step: why the vector cannot be judged, or null.
    static String dcbi(EmulatorHelper emu, AddressSpace ram, TcBank bank, long ea) {
        long lo = ea & ~31L;
        if (!bank.written.intersects(ram.getAddress(lo), ram.getAddress(lo + 31))) return null;
        return "dcbi at " + String.format("%08x", emu.getExecutionAddress().getOffset() - 4)
            + " discards block " + String.format("%08x", lo) + ", which the run wrote";
    }
}
