// fres / frsqrte estimates (tests/test_gc_estimate.py): TcEstimate (core/ghidra_shared/ppc) on operand bit patterns.
//
// usage: java TcEstimateStub FILE   FILE has one 16-digit hex operand per line; prints "OPERAND FRSQRTE FRES" per line
//   (all hex, 16 digits).
import java.nio.file.*;
import java.util.*;

public class TcEstimateStub {
    public static void main(String[] args) throws Exception {
        for (String ln : Files.readAllLines(Paths.get(args[0]))) {
            if (ln.isBlank()) continue;
            long b = Long.parseUnsignedLong(ln.trim(), 16);
            System.out.printf("%016x %016x %016x%n", b, TcEstimate.frsqrte(b), TcEstimate.fres(b));
        }
    }
}
