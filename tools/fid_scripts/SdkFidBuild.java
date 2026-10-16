// Builds one Function ID database from the programs of this project: one FID library per top-level project folder
// (an SDK library such as /shinobi, holding its objects), all under one version (the SDK release).
// Run headless with no program (no -import / -process), as a -preScript.
// Script args: 0 fidb path (created afresh; an existing file is replaced), 1 version (e.g. R10.1), 2 variant (e.g.
//   shc), 3 language id (the programs' language, e.g. SuperH4:LE:32:tcfix), 4 file listing the folders to ingest,
//   one per line.
// The database is created and opened directly (reflection on the package-private FidDB/FidFile entry points), so
// nothing is added to the user's Ghidra FID preferences.
// Prints FIDLIB <folder> programs=<n> attempted=<a> added=<b> excluded=<c> <disposition>=<count>...
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.feature.fid.db.*;
import ghidra.feature.fid.service.*;
import ghidra.framework.model.*;
import ghidra.program.model.lang.LanguageID;
import java.io.File;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

public class SdkFidBuild extends GhidraScript {
    void collect(DomainFolder folder, List<DomainFile> out) {
        for (DomainFile f : folder.getFiles()) {
            if ("Program".equals(f.getContentType())) out.add(f);
        }
        for (DomainFolder sub : folder.getFolders()) collect(sub, out);
    }

    static FidDB openForUpdate(File dbFile) throws Exception {
        Method create = FidDB.class.getDeclaredMethod("createNewFidDatabase", File.class);
        create.setAccessible(true);
        create.invoke(null, dbFile);
        Constructor<FidFile> ctor = FidFile.class.getDeclaredConstructor(FidFileManager.class, File.class,
            boolean.class);
        ctor.setAccessible(true);
        FidFile fidFile = ctor.newInstance(FidFileManager.getInstance(), dbFile, false);
        return fidFile.getFidDB(true);
    }

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        File dbFile = new File(a[0]);
        String version = a[1], variant = a[2];
        LanguageID lang = new LanguageID(a[3]);
        List<String> folders = new ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(a[4]))) {
            if (!line.trim().isEmpty()) folders.add(line.trim());
        }
        if (dbFile.exists() && !dbFile.delete()) throw new RuntimeException("cannot replace " + dbFile);
        DomainFolder root = state.getProject().getProjectData().getRootFolder();
        FidService service = new FidService();
        try (FidDB db = openForUpdate(dbFile)) {
            for (String name : folders) {
                DomainFolder folder = root.getFolder(name);
                if (folder == null) {
                    println("FIDLIB " + name + " missing");
                    continue;
                }
                List<DomainFile> programs = new ArrayList<>();
                collect(folder, programs);
                if (programs.isEmpty()) {
                    println("FIDLIB " + name + " programs=0");
                    continue;
                }
                FidPopulateResult r = service.createNewLibraryFromPrograms(db, name, version, variant, programs,
                    null, lang, null, null, monitor);
                StringBuilder sb = new StringBuilder();
                for (Map.Entry<FidPopulateResult.Disposition, Integer> e : r.getFailures().entrySet()) {
                    sb.append(' ').append(e.getKey()).append('=').append(e.getValue());
                }
                println("FIDLIB " + name + " programs=" + programs.size() + " attempted=" + r.getTotalAttempted()
                    + " added=" + r.getTotalAdded() + " excluded=" + r.getTotalExcluded() + sb);
            }
            db.saveDatabase("SdkFidBuild " + version, monitor);
        }
        println("FIDDB " + dbFile + " written");
    }
}
