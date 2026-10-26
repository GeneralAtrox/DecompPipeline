// tests/test_track.py: TcTrack.path, the registers an SH-4 step writes, on p-code built here in the shapes of the
// SH-4 SLEIGH's FPU instructions (no Ghidra program). One case per line of the file in args[0], "N: regs" per case
// ("null": a condition that reads something else, so every output counts):
//   P SHAPE SZ PR   the shape's path with the mode bits FPSCR_SZ and FPSCR_PR
// Shapes: fmov (`if (!(SZ == 0)) goto <doubleWidth>; FR6 = ..; goto <skip>; <doubleWidth> DR6 = ..`), skip (PR set:
// nothing; fldi1 FR4), next (`if (PR != 0) goto inst_next; FR8 = ..`), const (a decode-time constant that is false:
// the double form never runs; FR5 or DR4), other (a branch on T), plain (r4 and fv8: no branch).
import ghidra.program.model.address.*;
import ghidra.program.model.pcode.*;
import java.nio.file.*;
import java.util.*;

public class TcTrackPathStub {
    static final AddressSpace CONST = new GenericAddressSpace("const", 64, AddressSpace.TYPE_CONSTANT, 0);
    static final AddressSpace UNIQ = new GenericAddressSpace("unique", 32, AddressSpace.TYPE_UNIQUE, 1);
    static final AddressSpace REG = new GenericAddressSpace("register", 32, AddressSpace.TYPE_REGISTER, 2);
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 3);
    static final List<String> NAMES = Arrays.asList("FPSCR_SZ", "FPSCR_PR", "T", "fr4", "fr5", "fr6", "fr8", "dr4",
        "dr6", "fv8", "r4");
    static final List<PcodeOp> ops = new ArrayList<>();

    static Varnode c(long v) {
        return new Varnode(CONST.getAddress(v), 4);
    }

    static Varnode u(int n) {
        return new Varnode(UNIQ.getAddress(0x100L * n), 1);
    }

    static Varnode r(String name) {
        return new Varnode(REG.getAddress(0x10L * NAMES.indexOf(name)), 4);
    }

    static void op(int code, Varnode out, Varnode... in) {
        ops.add(new PcodeOp(new SequenceNumber(RAM.getAddress(0x8c010000L), ops.size()), code, in, out));
    }

    static void shape(String s) {
        ops.clear();
        switch (s) {
            case "fmov":
                op(PcodeOp.INT_EQUAL, u(0), r("FPSCR_SZ"), c(0));
                op(PcodeOp.BOOL_NEGATE, u(1), u(0));
                op(PcodeOp.CBRANCH, null, c(3), u(1));
                op(PcodeOp.LOAD, r("fr6"), c(0), r("r4"));
                op(PcodeOp.BRANCH, null, c(2));
                op(PcodeOp.LOAD, r("dr6"), c(0), r("r4"));
                break;
            case "skip":
                op(PcodeOp.INT_EQUAL, u(0), r("FPSCR_PR"), c(0));
                op(PcodeOp.BOOL_NEGATE, u(1), u(0));
                op(PcodeOp.CBRANCH, null, c(2), u(1));
                op(PcodeOp.COPY, r("fr4"), c(0x3f800000L));
                break;
            case "next":
                op(PcodeOp.INT_NOTEQUAL, u(0), r("FPSCR_PR"), c(0));
                op(PcodeOp.CBRANCH, null, new Varnode(RAM.getAddress(0x8c010002L), 4), u(0));
                op(PcodeOp.COPY, r("fr8"), c(0));
                break;
            case "const":
                op(PcodeOp.INT_EQUAL, u(0), r("FPSCR_PR"), c(0));
                op(PcodeOp.BOOL_NEGATE, u(1), u(0));
                op(PcodeOp.INT_EQUAL, u(2), c(1), c(0));
                op(PcodeOp.BOOL_AND, u(3), u(1), u(2));
                op(PcodeOp.CBRANCH, null, c(3), u(3));
                op(PcodeOp.FLOAT_NEG, r("fr5"), r("fr5"));
                op(PcodeOp.BRANCH, null, c(2));
                op(PcodeOp.FLOAT_NEG, r("dr4"), r("dr4"));
                break;
            case "other":
                op(PcodeOp.CBRANCH, null, c(2), r("T"));
                op(PcodeOp.COPY, r("fr4"), c(0));
                break;
            case "plain":
                op(PcodeOp.COPY, r("r4"), c(1));
                op(PcodeOp.COPY, r("fv8"), c(0));
                break;
            default:
                throw new IllegalArgumentException(s);
        }
    }

    public static void main(String[] args) throws Exception {
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.replaceAll("#.*", "").trim().split("\\s+");
            if (t[0].isEmpty()) continue;
            if (!t[0].equals("P")) throw new IllegalArgumentException(line);
            shape(t[1]);
            Map<String, Long> mode = Map.of("FPSCR_SZ", Long.parseLong(t[2]), "FPSCR_PR", Long.parseLong(t[3]));
            Set<String> w = TcTrack.path(ops.toArray(new PcodeOp[0]),
                v -> NAMES.get((int) (v.getOffset() / 0x10)), mode::get);
            System.out.println(n++ + ": " + (w == null ? "null" : String.join(",", new TreeSet<>(w))));
        }
    }
}
