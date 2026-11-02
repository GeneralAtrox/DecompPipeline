// Pass-through argument registers on PowerPC (not a script itself; each PowerPC target's TcPrepare calls it). One rule
// for every target whose target.json "shared_scripts" names "ppc" (core/paths.target_scripts).
//
// A call record compares the argument registers of the callee's own signature (TcPrepare.calleeRegs), so where the
// function under test passes a register on to a callee without writing it first, the record holds the function's
// entry value. Unless the function's own signature takes that register, the C cannot receive it and passes something
// else (Xbox 360 82782480: r7 and r8 to 8249d108; GameCube 800553b4: f1..f8). TcFlow's inputs look through callees
// to a fixed depth only, which need not reach what a callee's record compares. So the argument registers live at the
// entry when each call reads exactly the registers its record compares are inputs too, added like the other registers
// Ghidra's signature misses.
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.util.*;
import java.util.function.Predicate;
import java.util.function.Supplier;

public class TcPassThrough {
    // A target's TcFlow: f's entry inputs when each call or tail branch to an entry in `compared` reads exactly the
    // registers listed there, and one to any other target reads nothing.
    interface Flow {
        Set<String> inputsAt(Function f, Map<Address, Set<String>> compared) throws Exception;
    }

    // Offers `take` each argument register (argReg) live at fn's entry under the call records' registers (compared,
    // asked again each round) until a round takes none; take says whether the register was new to fn. A call to fn's
    // own entry compares fn's own registers, so a register taken can make another one live.
    static void take(Function fn, Flow flow, String argReg, Supplier<Map<Address, Set<String>>> compared,
                     Predicate<String> take) throws Exception {
        for (boolean grew = true; grew; ) {
            grew = false;
            for (String r : flow.inputsAt(fn, compared.get())) if (r.matches(argReg) && take.test(r)) grew = true;
        }
    }
}
