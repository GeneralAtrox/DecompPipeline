// One Ghidra per slot (core/ghidra_server.py): keeps the program open and runs TcPrepare once per request.
//
// Not in the stamped scripts folder (core/oracle_stamp hashes every *.java there): the server adds this folder to
// -scriptPath after the stamped one, so TcPrepare.java and its classes come from the stamped folder as before.
//
// Requests come on stdin, one per line: "REQ <id>\t<arg0>\t<arg1>..." (TcPrepare's script args, tab-separated), or
// "QUIT". Each request prints "TcServe: begin <id>", TcPrepare's own output, then
// "TcServe: done <id> ok <heap MB>" or "TcServe: done <id> error <message>". The Python side times each request
// out (it kills the whole server) and restarts a server after an error, every N requests and when idle.
// Every 50 requests the heap is measured after a full collection ("TcServe: heap <n> <MB>").
//
// Each request starts as a fresh Ghidra's one TcPrepare would:
// - a wall-clock oracle deadline (GameCube's TcVectors; Dreamcast's budgets are work, not time: TcBudget) is the
//   JVM's start plus the facts' oracle_deadline_s. A
//   fresh Ghidra spends its startup and the program's open out of that budget, then runs one TcPrepare. A server's
//   JVM is as old as the server, so each request gets a copy of the facts (its last argument) with
//   oracle_deadline_s moved on by the time since this server was ready. The stamp (core/oracle_stamp) hashes the
//   original facts file, so it is unchanged;
// - the scripts' static fields (e.g. TcBase.sqWindow, set by TcFold for a TcSq function) live in the stamped
//   folder's OSGi bundle, which stays loaded between requests. Their values from class initialisation are kept
//   before the first request and put back before every request (again when the bundle is reloaded). A static
//   whose initial value is mutable (a collection that would be filled in place) cannot be put back that way: the
//   server then refuses to start ("TcServe: mutable static ...") rather than serve stale state.
//@category DecompPipeline
import generic.jar.ResourceFile;
import ghidra.app.script.GhidraScript;
import ghidra.app.script.GhidraScriptUtil;
import ghidra.app.script.JavaScriptProvider;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class TcServe extends GhidraScript {
    private static final Pattern TOP = Pattern.compile(
        "(?m)^(?:public |final |abstract )*(?:class|interface|enum|record) (\\w+)");
    private ClassLoader loader;  // the stamped scripts' bundle, as TcPrepare is loaded from it
    private final Map<Field, Object> initial = new LinkedHashMap<>();

    @Override
    public void run() throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        Runtime rt = Runtime.getRuntime();
        resetStatics();  // refuses to start on a static it cannot put back
        println("TcServe: ready (" + initial.size() + " static fields kept)");
        long ready = System.currentTimeMillis();
        int n = 0;
        for (String line; (line = in.readLine()) != null; ) {
            if (line.equals("QUIT")) break;
            if (!line.startsWith("REQ ")) continue;
            String[] p = line.substring(4).split("\t", -1);
            String id = p[0];
            String[] args = Arrays.copyOfRange(p, 1, p.length);
            println("TcServe: begin " + id);
            Path facts = null;
            try {
                resetStatics();
                facts = shiftedFacts(args, (System.currentTimeMillis() - ready) / 1000);
                runScript("TcPrepare.java", args);
                long mb = (rt.totalMemory() - rt.freeMemory()) >> 20;
                println("TcServe: done " + id + " ok " + mb);
            } catch (Throwable e) {
                String msg = String.valueOf(e).replace('\n', ' ').replace('\r', ' ');
                println("TcServe: done " + id + " error " + msg);
            } finally {
                if (facts != null) Files.deleteIfExists(facts);
            }
            if (++n % 50 == 0) {
                System.gc();
                println("TcServe: heap " + n + " " + ((rt.totalMemory() - rt.freeMemory()) >> 20));
            }
        }
        println("TcServe: quit after " + n);
    }

    /** Put the scripts' static fields back to their values from class initialisation (kept on the first call and
     * whenever the bundle was reloaded, before any request ran in it). */
    private void resetStatics() throws Exception {
        ResourceFile src = GhidraScriptUtil.findScriptByName("TcPrepare.java");
        Class<?> prep = ((JavaScriptProvider) GhidraScriptUtil.getProvider(src)).loadClass(src, writer);
        ClassLoader cl = prep.getClassLoader();
        if (cl == loader) {
            for (Map.Entry<Field, Object> e : initial.entrySet()) e.getKey().set(null, e.getValue());
            return;
        }
        loader = cl;
        initial.clear();
        for (ResourceFile f : src.getParentFile().listFiles()) {
            if (!f.getName().endsWith(".java")) continue;
            Matcher m = TOP.matcher(new String(Files.readAllBytes(f.getFile(false).toPath()), StandardCharsets.UTF_8));
            while (m.find()) {
                try {
                    keep(Class.forName(m.group(1), true, cl));
                } catch (ClassNotFoundException e) {
                    // a declaration the regex misread (inside a comment or a string): no class by that name
                }
            }
        }
    }

    private void keep(Class<?> c) throws Exception {
        for (Field f : c.getDeclaredFields()) {
            int mod = f.getModifiers();
            if (!Modifier.isStatic(mod) || Modifier.isFinal(mod) || f.isSynthetic()) continue;
            f.setAccessible(true);
            Object v = f.get(null);
            if (!immutable(v)) {
                throw new IllegalStateException("TcServe: mutable static " + c.getName() + "." + f.getName()
                    + " (" + v.getClass().getName() + "): a server cannot reset it; make it final or immutable");
            }
            initial.put(f, v);
        }
        for (Class<?> inner : c.getDeclaredClasses()) keep(inner);
    }

    private static boolean immutable(Object v) {
        if (v == null || v instanceof Enum || v instanceof Class) return true;
        String name = v.getClass().getName();
        if (v instanceof CharSequence && !(v instanceof String)) return false;  // StringBuilder, StringBuffer
        return name.startsWith("java.lang.") || name.startsWith("java.util.ImmutableCollections");
    }

    /** TcPrepare's facts file argument (core/ghidra.py tc_args; tests/test_ghidra_server checks the place). Not the
     * last: spin and hw_ops follow it, so a "last" read shifted no deadline (waste audit 2, B). */
    static final int FACTS_ARG = 9;

    /** The facts file (args[FACTS_ARG]) copied with oracle_deadline_s plus waitedS; args then name the copy (null:
     * none, no facts file or no deadline in it). */
    private static Path shiftedFacts(String[] args, long waitedS) throws Exception {
        int i = FACTS_ARG;
        if (args.length <= i || args[i].equals("-")) return null;
        List<String> out = new ArrayList<>();
        boolean shifted = false;
        for (String line : Files.readAllLines(Paths.get(args[i]), StandardCharsets.UTF_8)) {
            String[] t = line.trim().split("\\s+", 2);
            if (t[0].equals("oracle_deadline_s") && t.length > 1 && Long.parseLong(t[1].trim()) > 0) {
                line = "oracle_deadline_s " + (Long.parseLong(t[1].trim()) + waitedS);
                shifted = true;
            }
            out.add(line);
        }
        if (!shifted) return null;
        Path copy = Files.createTempFile("tcserve_facts", ".txt");
        Files.write(copy, out, StandardCharsets.UTF_8);
        args[i] = copy.toString();
        return copy;
    }
}
