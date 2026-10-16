// Pre-analysis options for importing many small SDK library objects: turns off analyzers that FID does not need and
// that dominate the per-object time and size (data type archives, strings, DWARF, demangling, media, FID itself).
// Disassembly, functions, references and constant propagation (what FID hashes and relates) stay on.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import java.util.*;

public class SdkFidOptions extends GhidraScript {
    static final String[] OFF = {"Apply Data Archives", "ASCII Strings", "Function ID", "Demangler GNU",
        "Embedded Media", "DWARF", "Decompiler Switch Analysis", "Create Address Tables", "Stack",
        "Call Convention ID", "Variadic Function Signature Override", "Condense Filler Bytes"};

    @Override
    public void run() throws Exception {
        Map<String, String> opts = getCurrentAnalysisOptionsAndValues(currentProgram);
        for (String name : OFF) {
            if (opts.containsKey(name)) setAnalysisOption(currentProgram, name, "false");
        }
    }
}
