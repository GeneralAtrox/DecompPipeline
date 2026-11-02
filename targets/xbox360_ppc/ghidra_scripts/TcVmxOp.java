import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The vector executor's shared types (TcVmx.Op, TcVmx.Mem, NONE): the base of the TcVmx chain, split for the 400-line cap. */
abstract class TcVmxOp {
    public static final int NONE = Integer.MIN_VALUE;

    /** Guest memory. Addresses are the low 32 bits of the effective address. */
    public interface Mem {
        byte[] read(long ea, int n);

        void write(long ea, byte[] data);
    }

    /** A decoded vector instruction. Fields the instruction does not have are {@link #NONE}. */
    public static final class Op {
        public String mn;
        public int word;
        public int vd = NONE, vs = NONE, va = NONE, vb = NONE, vc = NONE;
        public int imm = NONE, simm = NONE, uimm = NONE, sh = NONE, perm = NONE, z = NONE, mask = NONE, rc = NONE;
        public int ra = NONE, rb = NONE, strm = NONE, t = NONE, all = NONE;
        /** Every field exactly as decode.py names it (for the decode test). */
        public final LinkedHashMap<String, Integer> f = new LinkedHashMap<>();
        /** Vector registers read / written, GPR numbers read (ra == 0 is the literal 0 and is not listed). */
        public int[] vRead = new int[0], vWrite = new int[0], gRead = new int[0], gWrite = new int[0];
        public boolean readsVscr, writesVscr, writesCr6, loads, stores;
        public String disasm;

        void set(String k, int val) {
            f.put(k, val);
            switch (k) {
                case "vd": vd = val; break;
                case "vs": vs = val; break;
                case "va": va = val; break;
                case "vb": vb = val; break;
                case "vc": vc = val; break;
                case "imm": imm = val; break;
                case "simm": simm = val; break;
                case "uimm": uimm = val; break;
                case "sh": sh = val; break;
                case "perm": perm = val; break;
                case "z": z = val; break;
                case "mask": mask = val; break;
                case "rc": rc = val; break;
                case "ra": ra = val; break;
                case "rb": rb = val; break;
                case "strm": strm = val; break;
                case "t": t = val; break;
                case "all": all = val; break;
                default: throw new IllegalStateException(k);
            }
        }

        @Override
        public String toString() {
            return disasm;
        }
    }
}
