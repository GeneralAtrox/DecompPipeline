// Register saves for the live-in analysis (TcFlow): stores that only keep a value to put it back, which are not reads
// of an input, and the loads that put it back, which are not changes. Not a script itself.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.util.regex.*;

public class TcSaves {
    final int fpulSlot; // the mask of the callee-saved register the function keeps in FPUL (0: none)
    final boolean context; // a context save (setjmp): it stores r15 and PR to memory, other than by a stack push

    // Scans the function's extent once.
    TcSaves(Listing listing, AddressSetView body) {
        int slot = 0;
        boolean fpulOther = false, r15 = false, pr = false;
        for (Instruction in : listing.getInstructions(body, true)) {
            String t = text(in);
            Matcher m = FPUL_IN.matcher(t), n = FPUL_OUT.matcher(t);
            if (m.matches() || n.matches()) {
                int r = TcFlow.maskOf("r" + (m.matches() ? m.group(1) : n.group(1)));
                if ((r & TcFlow.ISTATE) == 0 || (slot != 0 && slot != r)) fpulOther = true;
                slot = r;
            } else if (t.contains("fpul")) {
                fpulOther = true; // float, ftrc, fsca, flds, fsts, lds.l/sts.l FPUL: FPUL holds a value
            }
            r15 |= STORE_R15.matcher(t).matches();
            pr |= STORE_PR.matcher(t).matches();
        }
        fpulSlot = fpulOther ? 0 : slot;
        context = r15 && pr;
    }

    static String text(Instruction in) {
        return in.toString().toLowerCase().replaceFirst("^_", "");
    }

    static final Pattern FPUL_IN = Pattern.compile("^lds\\s+r(\\d+),fpul$");
    static final Pattern FPUL_OUT = Pattern.compile("^sts\\s+fpul,r(\\d+)$");
    static final Pattern STORE_R15 = Pattern.compile("^mov\\.l\\s+r15,@(?!-r15$).*");
    static final Pattern STORE_PR = Pattern.compile("^sts\\s+pr,r\\d+$|^sts\\.l\\s+pr,@-r([0-9]|1[0-4])$");
    static final Pattern STORE = Pattern.compile("^(?:mov\\.[bwl]|fmov\\.s|fmov)\\s+(f?r\\d+),@(.*)$");

    // A prologue save of a callee-saved register (`fmov.s fr12,@-r15`, `mov.l r14,@-r15`): the value is only stored
    // to be restored, so it is not a read of an input. Without this every function using them reads them. So is the
    // save of any register the code restores. A push of another register still counts as a read (it may be a stack
    // argument), but it too leaves the register saved. Also `lds r8,FPUL` when FPUL is that register's save slot
    // (fpulSlot: Toy Commander 8c04c928 keeps r8 there, `sts FPUL,r8` in each return's delay slot).
    boolean isSave(Instruction in) {
        String t = text(in);
        if (t.matches("(fmov\\.s|fmov|mov\\.l)\\s+(fr|r)\\d+,@-r15")) return true;
        Matcher m = FPUL_IN.matcher(t);
        return fpulSlot != 0 && m.matches() && TcFlow.maskOf("r" + m.group(1)) == fpulSlot;
    }

    // The matching epilogue restore (`fmov.s @r15+,fr12`, `mov.l @r15+,r14`, `sts FPUL,r8` of the FPUL slot): it puts
    // the saved entry value back, so it is not a change.
    boolean isRestore(Instruction in) {
        String t = text(in);
        if (t.matches("(fmov\\.s|fmov|mov\\.l)\\s+@r15\\+,(fr|r)\\d+")) return true;
        Matcher m = FPUL_OUT.matcher(t);
        return fpulSlot != 0 && m.matches() && TcFlow.maskOf("r" + m.group(1)) == fpulSlot;
    }

    // In a context save (setjmp-like: Toy Commander 8c09cdcc stores r15, PR, r0..r14, fr0..fr15 and the system
    // registers into a buffer and returns 0), the register a store keeps (its value operand, not one in its address):
    // the code records it to be restored later, it does not compute with it, so it is not a read of an input. Its
    // callers would otherwise read every register through it (buInit's fr4..fr15 and r8..r10). Returns its name, or
    // null.
    String contextStored(Instruction in) {
        if (!context) return null;
        Matcher m = STORE.matcher(text(in));
        if (!m.matches() || m.group(2).matches(".*\\b" + m.group(1) + "\\b.*")) return null;
        return m.group(1);
    }
}
