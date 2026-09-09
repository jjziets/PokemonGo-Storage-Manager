package pokemgr.tools;

import android.os.SystemClock;
import java.io.BufferedReader;
import java.io.FileReader;
import java.io.InputStreamReader;

/** One read-only clock sample. No app, display, input, or settings operations. */
public final class ClockSample {
    private ClockSample() {}

    public static void main(String[] args) throws Exception {
        boolean loop = args.length == 1 && "--loop".equals(args[0]);
        if (args.length != 1 || (!loop && !args[0].matches("[0-9a-f]{32}"))) {
            throw new IllegalArgumentException("Expected one request nonce");
        }
        String bootId;
        try (BufferedReader reader = new BufferedReader(
                new FileReader("/proc/sys/kernel/random/boot_id"))) {
            bootId = reader.readLine();
        }
        if (bootId == null || !bootId.matches(
                "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")) {
            throw new IllegalStateException("Boot identity unavailable");
        }

        if (loop) {
            BufferedReader input = new BufferedReader(new InputStreamReader(System.in, "US-ASCII"));
            System.out.println("POKEMGR_CLOCK_READY_V1");
            System.out.flush();
            String nonce;
            while ((nonce = input.readLine()) != null) {
                if (!nonce.matches("[0-9a-f]{32}")) {
                    throw new IllegalArgumentException("Invalid request nonce");
                }
                sample(bootId, nonce);
            }
        } else {
            sample(bootId, args[0]);
        }
    }

    private static void sample(String bootId, String nonce) {
        // Android System.nanoTime is CLOCK_MONOTONIC. BOOTTIME is sampled
        // between two readings only to detect suspend across calibrations.
        long before = System.nanoTime();
        long bootTime = SystemClock.elapsedRealtimeNanos();
        long after = System.nanoTime();
        System.out.println("POKEMGR_CLOCK_V1 " + nonce + " " + bootId + " "
                + before + " " + bootTime + " " + after);
        System.out.flush();
    }
}
