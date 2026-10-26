// The packet's asm.txt for a PowerPC function (TcPrepare, TcExport): one instruction per line, block labels, and the
// address each r2/r13-relative or lis/addi operand names (with the word there), the callee of every call, and the
// register save helpers (TcFlow.isSaveHelper) the oracle runs inline. Not a script itself.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.util.regex.*;

public class TcAsm {
    static final Pattern SDA = Pattern.compile("(-?0x[0-9a-f]+|-?\\d+)\\((r2|r13)\\)");
    static final Pattern SDA_ADDI = Pattern.compile("^addi r\\d+,(r2|r13),(-?0x[0-9a-f]+|-?\\d+)$");

    static long parse(String s) {
        boolean neg = s.startsWith("-");
        String t = neg ? s.substring(1) : s;
        long v = t.startsWith("0x") ? Long.parseLong(t.substring(2), 16) : Long.parseLong(t);
        return neg ? -v : v;
    }

    // The address an r2/r13-relative operand names (fixed registers from the facts), or -1.
    static long sdaAddress(TcBase b, String text) {
        Matcher m = SDA.matcher(text);
        Matcher a = SDA_ADDI.matcher(text);
        String reg, disp;
        if (m.find()) {
            reg = m.group(2);
            disp = m.group(1);
        } else if (a.find()) {
            reg = a.group(1);
            disp = a.group(2);
        } else {
            return -1;
        }
        Long base = b.facts.fixed.get(reg);
        return base == null ? -1 : (base + parse(disp)) & 0xffffffffL;
    }

    // Addresses the instruction names as data: Ghidra's references, else the SDA operand.
    static List<Long> named(TcBase b, Instruction in) {
        List<Long> out = b.dataRefs(in);
        if (out.isEmpty()) {
            long s = sdaAddress(b, in.toString().toLowerCase());
            if (s >= 0) out.add(s);
        }
        return out;
    }

    static String text(TcBase b, Function fn, AddressSetView body, Set<Long> labels) {
        for (String r : new String[] {"r2", "r13"}) {  // TcExport has no facts: the values TcSetup pinned
            java.math.BigInteger v = b.prog().getProgramContext().getValue(b.prog().getRegister(r), fn.getEntryPoint(), false);
            if (v != null && !b.facts.fixed.containsKey(r)) b.facts.fixed.put(r, v.longValue());
        }
        StringBuilder asm = new StringBuilder();
        asm.append("; fn_").append(TcBase.hex(fn.getEntryPoint().getOffset())).append("  entry ")
            .append(fn.getEntryPoint()).append("  ").append(body.getNumAddresses()).append(" bytes\n");
        asm.append("; PowerPC 750CL (Gekko), big endian. At entry r1 = 0x").append(TcBase.hex(TcBase.SP));
        for (Map.Entry<String, Long> e : b.facts.fixed.entrySet()) {
            if (e.getKey().matches("r2|r13")) asm.append(", ").append(e.getKey()).append(" = 0x").append(TcBase.hex(e.getValue()));
        }
        asm.append(" (r2/r13: the SDA bases, fixed for the whole program).\n");
        Listing listing = b.prog().getListing();
        for (Instruction in : listing.getInstructions(body, true)) {
            long ia = in.getAddress().getOffset();
            boolean label = labels != null ? labels.contains(ia) : in.getReferenceIteratorTo().hasNext();
            if (label && ia != fn.getEntryPoint().getOffset()) asm.append(String.format("LAB_%s:\n", TcBase.hex(ia)));
            StringBuilder note = new StringBuilder();
            for (long g : named(b, in)) note.append("   ; [").append(TcBase.hex(g)).append("] ").append(b.describeConst(g));
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isCall()) continue;
                Address to = r.getToAddress();
                if (TcFlow.isSaveHelper(listing, to)) note.append("   ; register save/restore helper ").append(to)
                    .append(": it only moves registers to or from the frame; no tc_call for it");
                else note.append("   ; call ").append(to);
            }
            if (in.getFlowType().isJump() && in.getFlowType().isComputed()) note.append("   ; computed jump (switch)");
            asm.append(String.format("  %s  %-34s%s\n", TcBase.hex(ia), in.toString(), note));
        }
        return asm.toString();
    }
}
