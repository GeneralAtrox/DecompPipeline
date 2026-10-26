// The packet's asm.txt for an Xbox 360 function (TcPrepare, TcExport): one instruction per line, block labels, and
// the address each r13-relative or lis-paired operand names (with the word there), the callee of every call (an
// import thunk by library and ordinal), and the register save/restore helpers the oracle runs inline. Not a script
// itself.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.util.regex.*;

public class TcAsm {
    static final Pattern SDA = Pattern.compile("(-?0x[0-9a-f]+|-?\\d+)\\((r13)\\)");
    static final Pattern SDA_ADDI = Pattern.compile("^addi r\\d+,(r13),(-?0x[0-9a-f]+|-?\\d+)$");
    static final Pattern LIS = Pattern.compile("^lis (r\\d+),(-?0x[0-9a-f]+|-?\\d+)$");
    static final Pattern LO_ADDI = Pattern.compile("^(?:addi|addic) r\\d+,(r\\d+),(-?0x[0-9a-f]+|-?\\d+)$");
    static final Pattern LO_ORI = Pattern.compile("^ori r\\d+,(r\\d+),(0x[0-9a-f]+|\\d+)$");
    static final Pattern LO_MEM = Pattern.compile("^[a-z]+ [rf]\\d+,(-?0x[0-9a-f]+|-?\\d+)\\((r\\d+)\\)$");

    static long parse(String s) {
        boolean neg = s.startsWith("-");
        String t = neg ? s.substring(1) : s;
        long v = t.startsWith("0x") ? Long.parseLong(t.substring(2), 16) : Long.parseLong(t);
        return neg ? -v : v;
    }

    // The address an r13-relative operand names (the fixed r13 from the facts), or -1.
    static long sdaAddress(TcBase b, String text) {
        Matcher m = SDA.matcher(text);
        Matcher a = SDA_ADDI.matcher(text);
        String disp;
        if (m.find()) disp = m.group(1);
        else if (a.find()) disp = a.group(2);
        else return -1;
        Long base = b.facts.fixed.get("r13");
        return base == null ? -1 : (base + parse(disp)) & 0xffffffffL;
    }

    // lis/addi, lis/ori and lis/load-store pairs in one basic block: the address each low half completes.
    static Map<Long, Long> lisPairs(TcBase b, AddressSetView body, Set<Long> labels) {
        Map<Long, Long> out = new HashMap<>();
        Map<String, Long> hi = new HashMap<>();
        for (Instruction in : b.prog().getListing().getInstructions(body, true)) {
            long ia = in.getAddress().getOffset();
            if (labels != null && labels.contains(ia)) hi.clear();
            String t = in.toString().toLowerCase().replaceAll("\\s+", " ");
            Matcher m;
            String reg = null;
            long lo = 0;
            if ((m = LO_ADDI.matcher(t)).matches() || (m = LO_ORI.matcher(t)).matches()) {
                reg = m.group(1);
                lo = parse(m.group(2));
            } else if ((m = LO_MEM.matcher(t)).matches()) {
                reg = m.group(2);
                lo = parse(m.group(1));
            }
            if (reg != null && hi.containsKey(reg)) out.put(ia, (hi.get(reg) + lo) & 0xffffffffL);
            if ((m = LIS.matcher(t)).matches()) {
                hi.put(m.group(1), (parse(m.group(2)) << 16) & 0xffffffffL);
                continue;
            }
            for (Object o : in.getResultObjects()) {  // any other write of a register ends its pair
                if (o instanceof ghidra.program.model.lang.Register) hi.remove(o.toString().toLowerCase());
            }
            if (in.getFlowType().isCall()) hi.clear();
        }
        return out;
    }

    // Addresses the instruction names as data: Ghidra's references, else the r13 operand or the lis pair.
    static List<Long> named(TcBase b, Instruction in, Map<Long, Long> pairs) {
        List<Long> out = b.dataRefs(in);
        if (out.isEmpty()) {
            long s = sdaAddress(b, in.toString().toLowerCase());
            Long p = pairs == null ? null : pairs.get(in.getAddress().getOffset());
            if (s >= 0) out.add(s);
            else if (p != null && b.mapped(p)) out.add(p);
        }
        return out;
    }

    static String text(TcBase b, Function fn, AddressSetView body, Set<Long> labels) {
        java.math.BigInteger r13 = b.prog().getProgramContext().getValue(b.prog().getRegister("r13"), fn.getEntryPoint(), false);
        if (r13 != null && !b.facts.fixed.containsKey("r13")) b.facts.fixed.put("r13", r13.longValue());  // TcExport
        StringBuilder asm = new StringBuilder();
        asm.append("; fn_").append(TcBase.hex(fn.getEntryPoint().getOffset())).append("  entry ")
            .append(fn.getEntryPoint()).append("  ").append(body.getNumAddresses()).append(" bytes\n");
        asm.append("; Xbox 360 Xenon PowerPC: 64-bit registers, 32-bit addresses, big endian. At entry r1 = 0x")
            .append(TcBase.hex(TcBase.SP));
        Long fixed13 = b.facts.fixed.get("r13");
        if (fixed13 != null) asm.append(", r13 = 0x").append(TcBase.hex(fixed13)).append(" (the processor block)");
        asm.append(".\n");
        Listing listing = b.prog().getListing();
        FunctionManager fm = b.prog().getFunctionManager();
        Map<Long, Long> pairs = lisPairs(b, body, labels);
        for (Instruction in : listing.getInstructions(body, true)) {
            long ia = in.getAddress().getOffset();
            boolean label = labels != null ? labels.contains(ia) : in.getReferenceIteratorTo().hasNext();
            if (label && ia != fn.getEntryPoint().getOffset()) asm.append(String.format("LAB_%s:\n", TcBase.hex(ia)));
            StringBuilder note = new StringBuilder();
            for (long g : named(b, in, pairs)) note.append("   ; [").append(TcBase.hex(g)).append("] ").append(b.describeConst(g));
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow()) continue;
                Address to = r.getToAddress();
                Function g = fm.getFunctionContaining(to);
                if (TcBase.isHelper(g)) {
                    note.append("   ; register save/restore helper ").append(b.prog().getSymbolTable().getPrimarySymbol(to))
                        .append(": it only moves registers to or from the frame; no tc_call for it");
                } else if (TcBase.isImport(g)) {
                    note.append("   ; call import ").append(g.getName()).append(" (tc_call to its thunk 0x")
                        .append(TcBase.hex(to.getOffset())).append(")");
                } else if (r.getReferenceType().isCall()) {
                    note.append("   ; call ").append(to);
                } else if (!body.contains(to) && g != null) {
                    note.append("   ; tail call ").append(to);
                }
            }
            if (in.getFlowType().isJump() && in.getFlowType().isComputed()) note.append("   ; computed jump (switch)");
            asm.append(String.format("  %s  %-34s%s\n", TcBase.hex(ia), in.toString(), note));
        }
        return asm.toString();
    }
}
