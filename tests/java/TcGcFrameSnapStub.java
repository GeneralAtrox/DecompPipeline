// tests/test_gc_refused_harness.py's view of the GameCube TcStrict's snapshot extent and saved-register scan.
// usage: java TcGcFrameSnapStub   prints "case: value" lines. The frame is modelled on Skies of Arcadia 800a13d8:
// stwu r1,-0x100(r1), r27..r31 saved by stmw at r1+0xec, f31 at r1+0xf8; it stores a two-vertex array at r1+8..0x17
// and passes r1+8 to 80291bb4, a 0x40-byte buffer at r1+0x20, and a local at r1+0x70 it never passes on.
import java.util.*;

public class TcGcFrameSnapStub {
    public static void main(String[] args) {
        long sp = TcBase.SP - 0x100;
        Set<Long> st = new HashSet<>();
        for (long a = 8; a < 0x18; a++) st.add(sp + a);
        for (long a = 0x20; a < 0x60; a++) st.add(sp + a);
        for (long a = 0x70; a < 0x74; a++) st.add(sp + a);
        for (long a = 0xe0; a < 0x100; a++) st.add(sp + a);   // a local at 0xe0..0xeb right below the saves
        for (long a = 0; a < 0x80; a++) st.add(0x81001000L + a);  // a heap run
        List<String> fr = List.of("stwu r1,-0x100(r1)", "mflr r0", "stw r0,0x104(r1)", "stmw r27,0xec(r1)",
            "stfd f31,0xf8(r1)", "stw r0,0x8(r1)", "addi r5,r1,0x8");
        int lo = TcStrict.saveLo(fr);
        System.out.println("saveLo_stmw: " + Integer.toHexString(lo));
        System.out.println("vertex_array: " + TcStrict.extent(sp + 8, sp, lo, st::contains));
        System.out.println("buffer: " + TcStrict.extent(sp + 0x20, sp, lo, st::contains));
        System.out.println("next_to_saves: " + TcStrict.extent(sp + 0xe0, sp, lo, st::contains));
        System.out.println("in_saves: " + TcStrict.extent(sp + 0xf0, sp, lo, st::contains));
        System.out.println("caller_frame: " + TcStrict.extent(TcBase.SP + 8, sp, lo, st::contains));
        System.out.println("below_sp: " + TcStrict.extent(sp - 0x10, sp, lo, st::contains));
        System.out.println("heap: " + TcStrict.extent(0x81001000L, sp, lo, st::contains));
        System.out.println("saveLo_helper: " + Integer.toHexString(TcStrict.saveLo(List.of("stwu r1,-0x60(r1)",
            "addi r11,r1,0x60", "bl 0x802b0a2c", "stfd f30,-0x10(r11)", "stfd f31,-0x8(r11)", "stw r29,-0x24(r11)"))));
        System.out.println("saveLo_before_stwu: " + Integer.toHexString(TcStrict.saveLo(List.of("stw r31,-0x4(r1)",
            "stwu r1,-0x20(r1)"))));
        System.out.println("saveLo_none: " + (TcStrict.saveLo(List.of("stwu r1,-0x20(r1)", "stw r3,0x8(r1)",
            "stw r0,0x24(r1)")) == Integer.MAX_VALUE));
    }
}
