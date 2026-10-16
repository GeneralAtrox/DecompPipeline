// Runs Function ID against the current program with one or more databases, each queried on its own so every match
// keeps the release it came from. Writes nothing to the program.
// Script args: 0 output TSV, 1 score threshold (e.g. 10; the caller applies FID's own 14.6 later), 2 lowest entry
//   address to report (hex), then TAG PATH.fidb pairs (two arguments each: a .bat launcher splits at '=').
// Output columns: addr  tag  status  code_units  name  family  version  object  overall  primary  child  parent
//   status: match (one row per candidate), nomatch (hashed, no candidate), small (too few instructions to hash).
// The databases are opened directly (reflection on the package-private FidFile/FidQueryService constructors), so
// nothing is added to the user's Ghidra FID preferences.
// Prints FIDAPPLY <tag> functions=<n> hashed=<h> matched=<m>
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.feature.fid.db.*;
import ghidra.feature.fid.service.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Language;
import ghidra.program.model.listing.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;

public class SdkFidApply extends GhidraScript {
    static FidQueryService open(File dbFile, Language lang) throws Exception {
        Constructor<FidFile> fc = FidFile.class.getDeclaredConstructor(FidFileManager.class, File.class,
            boolean.class);
        fc.setAccessible(true);
        FidFile ff = fc.newInstance(FidFileManager.getInstance(), dbFile, false);
        Constructor<FidQueryService> qc = FidQueryService.class.getDeclaredConstructor(Set.class, Language.class,
            boolean.class);
        qc.setAccessible(true);
        return qc.newInstance(new HashSet<>(List.of(ff)), lang, false);
    }

    static String f2(float v) {
        return String.format(Locale.ROOT, "%.2f", v);
    }

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        float threshold = Float.parseFloat(a[1]);
        long lowest = Long.parseLong(a[2], 16);
        FidService service = new FidService();
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[0]), "UTF-8"))) {
            pw.println("addr\ttag\tstatus\tcode_units\tname\tfamily\tversion\tobject\toverall\tprimary\tchild\tparent");
            for (int i = 3; i + 1 < a.length; i += 2) {
                String tag = a[i];
                File db = new File(a[i + 1]);
                int n = 0, hashed = 0, matched = 0;
                try (FidQueryService q = open(db, currentProgram.getLanguage())) {
                    FidProgramSeeker seeker = service.getProgramSeeker(currentProgram, q, threshold);
                    for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                        monitor.checkCancelled();
                        Address e = f.getEntryPoint();
                        if (e.getOffset() < lowest) continue;
                        n++;
                        String addr = String.format("%08x", e.getOffset());
                        FidSearchResult r = seeker.searchFunction(f, monitor);
                        if (r == null) {
                            pw.println(addr + "\t" + tag + "\tsmall\t\t\t\t\t\t\t\t\t");
                            continue;
                        }
                        hashed++;
                        int units = r.hashQuad.getCodeUnitSize();
                        if (r.matches == null || r.matches.isEmpty()) {
                            pw.println(addr + "\t" + tag + "\tnomatch\t" + units + "\t\t\t\t\t\t\t\t");
                            continue;
                        }
                        matched++;
                        for (FidMatch m : r.matches) {
                            FunctionRecord fr = m.getFunctionRecord();
                            LibraryRecord lr = m.getLibraryRecord();
                            pw.println(addr + "\t" + tag + "\tmatch\t" + units + "\t" + fr.getName() + "\t"
                                + lr.getLibraryFamilyName() + "\t" + lr.getLibraryVersion() + "\t"
                                + fr.getDomainPath() + "\t" + f2(m.getOverallScore()) + "\t"
                                + f2(m.getPrimaryFunctionCodeUnitScore()) + "\t"
                                + f2(m.getChildFunctionCodeUnitScore()) + "\t"
                                + f2(m.getParentFunctionCodeUnitScore()));
                        }
                    }
                }
                println("FIDAPPLY " + tag + " functions=" + n + " hashed=" + hashed + " matched=" + matched);
            }
        }
    }
}
