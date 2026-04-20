import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.TreeSet;

public class RelativeOffsetRedBlackTreeDemo {
    public static void main(String[] args) {
        basicDemo();
        randomizedVerification();
        System.out.println("All checks passed.");
    }

    private static void basicDemo() {
        RelativeOffsetRedBlackTree tree = new RelativeOffsetRedBlackTree();
        tree.insert(10);
        tree.insert(20);
        tree.insert(30);
        tree.insert(40);
        tree.insert(50);

        // 把 >= 30 的所有 key +7 => [10, 20, 37, 47, 57]
        tree.addToKeysFrom(30, 7);
        List<Long> keys = tree.keysInOrder();
        assertEquals(keys, List.of(10L, 20L, 37L, 47L, 57L), "basicDemo shift +7");

        // 把 >= 37 的所有 key -5 => [10, 20, 32, 42, 52]
        tree.addToKeysFrom(37, -5);
        keys = tree.keysInOrder();
        assertEquals(keys, List.of(10L, 20L, 32L, 42L, 52L), "basicDemo shift -5");
    }

    private static void randomizedVerification() {
        RelativeOffsetRedBlackTree tree = new RelativeOffsetRedBlackTree();
        TreeSet<Long> ref = new TreeSet<>();
        Random random = new Random(7);

        for (int i = 0; i < 10_000; i++) {
            int op = random.nextInt(100);
            if (op < 55 || ref.isEmpty()) {
                long candidate = random.nextInt(2000) - 1000L;
                if (!ref.contains(candidate)) {
                    ref.add(candidate);
                    tree.insert(candidate);
                }
            } else {
                long from = random.nextInt(2000) - 1000L;
                long delta;
                do {
                    delta = random.nextInt(41) - 20L;
                } while (delta == 0L);

                Long first = ref.ceiling(from);
                if (first == null) {
                    tree.addToKeysFrom(from, delta);
                } else {
                    Long predecessor = ref.lower(first);
                    boolean valid = predecessor == null || first + delta > predecessor;
                    if (valid) {
                        tree.addToKeysFrom(from, delta);
                        applyShift(ref, from, delta);
                    } else {
                        boolean thrown = false;
                        try {
                            tree.addToKeysFrom(from, delta);
                        } catch (IllegalArgumentException expected) {
                            thrown = true;
                        }
                        if (!thrown) {
                            throw new AssertionError("Expected IllegalArgumentException but not thrown");
                        }
                    }
                }
            }

            List<Long> expected = new ArrayList<>(ref);
            List<Long> actual = tree.keysInOrder();
            assertEquals(actual, expected, "step " + i);
        }
    }

    private static void applyShift(TreeSet<Long> ref, long fromInclusive, long delta) {
        List<Long> tail = new ArrayList<>(ref.tailSet(fromInclusive, true));
        for (Long x : tail) {
            ref.remove(x);
        }
        for (Long x : tail) {
            ref.add(x + delta);
        }
    }

    private static void assertEquals(List<Long> actual, List<Long> expected, String title) {
        if (!actual.equals(expected)) {
            throw new AssertionError(title + " mismatch. expected=" + expected + ", actual=" + actual);
        }
    }
}
