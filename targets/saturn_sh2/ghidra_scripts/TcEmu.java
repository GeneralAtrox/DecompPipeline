// The oracle's emulator for the SH-2: Ghidra's EmulatorHelper, except that a register the language does not have
// (the SH-4 FPU the shared scripts name: fr0..fr15, xf0..xf15, FPSCR and its fields, FPUL) reads as 0 and a write
// to it is dropped. The SH-2 has no FPU, so those values are 0 at entry, in every call record and at exit, exactly
// as the C runner (whose FPU state stays 0) reports them. Not a script itself; TcOracle makes one per vector.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.listing.Program;
import java.math.BigInteger;

public class TcEmu extends EmulatorHelper {
    final Program prog;

    TcEmu(Program prog) {
        super(prog);
        this.prog = prog;
    }

    boolean has(String name) {
        return prog.getRegister(name) != null;
    }

    @Override
    public BigInteger readRegister(String name) {
        return has(name) ? super.readRegister(name) : BigInteger.ZERO;
    }

    @Override
    public void writeRegister(String name, BigInteger value) {
        if (has(name)) super.writeRegister(name, value);
    }

    @Override
    public void writeRegister(String name, long value) {
        if (has(name)) super.writeRegister(name, value);
    }
}
