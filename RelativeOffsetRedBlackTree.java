import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * 红黑树（key 为 long），节点存储的是“相对左父节点”的偏移，而不是绝对 key。
 *
 * 左父节点规则：
 * 1) 当前节点若是父节点的右子节点，则父节点是它的左父节点；
 * 2) 否则沿父链向上，直到某个祖先是其父节点的右子节点，该父节点即左父节点；
 * 3) 若不存在，则使用虚拟基准 0。
 *
 * 在递归视角下可等价为：
 * - 左子树节点沿用当前节点的左父基准；
 * - 右子树节点以当前节点绝对 key 作为左父基准。
 *
 * 因此，addToKeysFrom(from, delta) 可以在 O(log n) 内完成：
 * - 若当前节点 key >= from：当前节点 delta += delta，继续进入左子树；
 *   （右子树会因“基准抬升”自动整体平移）
 * - 若当前节点 key < from：继续进入右子树。
 */
public class RelativeOffsetRedBlackTree {
    private static final boolean RED = true;
    private static final boolean BLACK = false;

    private static final class BoundResult {
        private final Node node;
        private final long key;
        private final Long predecessor;

        BoundResult(Node node, long key, Long predecessor) {
            this.node = node;
            this.key = key;
            this.predecessor = predecessor;
        }
    }

    public static final class Node {
        private long delta; // key = leftParentAbsoluteKey + delta
        private int leftCount; // 左子树节点总数（用于下标访问）
        private boolean color;
        private Node left;
        private Node right;
        private Node parent;

        Node(long delta, boolean color) {
            this.delta = delta;
            this.color = color;
        }

        public long getDelta() {
            return delta;
        }

        public int getLeftCount() {
            return leftCount;
        }
    }

    private Node root;
    private int size;

    public int size() {
        return size;
    }

    public boolean isEmpty() {
        return size == 0;
    }

    /**
     * 返回第一个 key >= target 的节点，不存在则返回 null。
     */
    public Node lowerBound(long target) {
        return findBound(target, false).node;
    }

    /**
     * 返回第一个 key > target 的节点，不存在则返回 null。
     */
    public Node upperBound(long target) {
        return findBound(target, true).node;
    }

    /**
     * 按中序顺序返回第 index 个节点（0-based）。
     */
    public Node at(int index) {
        if (index < 0 || index >= size) {
            throw new IndexOutOfBoundsException("index=" + index + ", size=" + size);
        }
        Node cur = root;
        int rank = index;
        while (cur != null) {
            if (rank < cur.leftCount) {
                cur = cur.left;
            } else if (rank == cur.leftCount) {
                return cur;
            } else {
                rank -= cur.leftCount + 1;
                cur = cur.right;
            }
        }
        throw new IllegalStateException("Broken leftCount index path");
    }

    /**
     * 按中序顺序返回第 index 个 key（0-based）。
     */
    public long keyAt(int index) {
        if (index < 0 || index >= size) {
            throw new IndexOutOfBoundsException("index=" + index + ", size=" + size);
        }
        Node cur = root;
        int rank = index;
        long base = 0L;
        while (cur != null) {
            long key = base + cur.delta;
            if (rank < cur.leftCount) {
                cur = cur.left;
            } else if (rank == cur.leftCount) {
                return key;
            } else {
                rank -= cur.leftCount + 1;
                base = key;
                cur = cur.right;
            }
        }
        throw new IllegalStateException("Broken leftCount index path");
    }

    /**
     * 返回指定节点对应的绝对 key。
     */
    public long keyOf(Node node) {
        if (node == null) {
            throw new IllegalArgumentException("node is null");
        }
        ArrayDeque<Node> path = new ArrayDeque<>();
        Node cur = node;
        while (cur != null) {
            path.push(cur);
            cur = cur.parent;
        }

        long base = 0L;
        Node current = path.pop();
        while (true) {
            long key = base + current.delta;
            if (path.isEmpty()) {
                return key;
            }
            Node child = path.pop();
            if (child == current.right) {
                base = key;
            }
            current = child;
        }
    }

    public boolean contains(long key) {
        Node cur = root;
        long base = 0L;
        while (cur != null) {
            long curKey = base + cur.delta;
            if (key < curKey) {
                cur = cur.left;
            } else if (key > curKey) {
                base = curKey;
                cur = cur.right;
            } else {
                return true;
            }
        }
        return false;
    }

    public void insert(long key) {
        if (root == null) {
            root = new Node(key, BLACK); // base = 0
            size = 1;
            return;
        }

        Node parent = null;
        Node cur = root;
        long base = 0L;
        boolean goRight = false;
        List<Node> leftPath = new ArrayList<>();

        while (cur != null) {
            parent = cur;
            long curKey = base + cur.delta;
            if (key < curKey) {
                leftPath.add(cur);
                cur = cur.left;
                goRight = false;
            } else if (key > curKey) {
                base = curKey;
                cur = cur.right;
                goRight = true;
            } else {
                throw new IllegalArgumentException("Duplicate key: " + key);
            }
        }

        Node z = new Node(key - base, RED);
        z.parent = parent;
        if (goRight) {
            parent.right = z;
        } else {
            parent.left = z;
        }
        for (Node n : leftPath) {
            n.leftCount++;
        }
        fixAfterInsert(z);
        size++;
    }

    /**
     * 将所有 key >= fromInclusive 的节点 key 增加 delta（delta 可为负）。
     *
     * 注意：若 delta < 0 且导致“受影响段最小 key”小于等于前驱 key，会破坏有序性，
     * 本实现会抛 IllegalArgumentException。
     */
    public void addToKeysFrom(long fromInclusive, long delta) {
        if (root == null || delta == 0L) {
            return;
        }

        BoundResult firstAffected = findBound(fromInclusive, false);
        if (firstAffected.node == null) {
            return;
        }
        if (firstAffected.predecessor != null && firstAffected.key + delta <= firstAffected.predecessor) {
            throw new IllegalArgumentException(
                    "Shift would break BST order: firstAffected=" + firstAffected.key
                            + ", predecessor=" + firstAffected.predecessor
                            + ", delta=" + delta);
        }

        Node cur = root;
        long base = 0L;
        while (cur != null) {
            long key = base + cur.delta;
            if (key >= fromInclusive) {
                // 当前节点及其右子树全部受影响：只修改当前节点即可把“右侧基准”整体平移
                cur.delta += delta;
                cur = cur.left;
            } else {
                base = key;
                cur = cur.right;
            }
        }
    }

    public List<Long> keysInOrder() {
        List<Long> result = new ArrayList<>(size);
        inOrder(root, 0L, result);
        return result;
    }

    @Override
    public String toString() {
        return keysInOrder().toString();
    }

    // -------------------- 内部实现 --------------------

    private void inOrder(Node node, long base, List<Long> out) {
        ArrayDeque<Node> nodeStack = new ArrayDeque<>();
        ArrayDeque<Long> baseStack = new ArrayDeque<>();
        Node cur = node;
        long curBase = base;

        while (cur != null || !nodeStack.isEmpty()) {
            while (cur != null) {
                nodeStack.push(cur);
                baseStack.push(curBase);
                cur = cur.left;
            }

            cur = nodeStack.pop();
            long nodeBase = baseStack.pop();
            long key = nodeBase + cur.delta;
            out.add(key);

            curBase = key;
            cur = cur.right;
        }
    }

    private BoundResult findBound(long target, boolean strictGreater) {
        Node cur = root;
        long base = 0L;
        Node candidateNode = null;
        long candidateKey = 0L;
        Long predecessor = null;
        while (cur != null) {
            long key = base + cur.delta;
            boolean isCandidate = strictGreater ? key > target : key >= target;
            if (isCandidate) {
                candidateNode = cur;
                candidateKey = key;
                cur = cur.left;
            } else {
                predecessor = key;
                base = key;
                cur = cur.right;
            }
        }
        return new BoundResult(candidateNode, candidateKey, predecessor);
    }

    private void rotateLeft(Node x) {
        Node y = x.right;
        if (y == null) {
            return;
        }
        int xLeftCount = x.leftCount;
        int yLeftCount = y.leftCount;

        x.right = y.left;
        if (y.left != null) {
            y.left.parent = x;
        }

        y.parent = x.parent;
        if (x.parent == null) {
            root = y;
        } else if (x == x.parent.left) {
            x.parent.left = y;
        } else {
            x.parent.right = y;
        }

        y.left = x;
        x.parent = y;

        // y 继承了 x 原先的左父基准，因此 y.delta 需要补上 x.delta。
        y.delta += x.delta;
        // 旋转后 y 的左子树变为整棵 x 子树。
        y.leftCount = xLeftCount + yLeftCount + 1;
    }

    private void rotateRight(Node y) {
        Node x = y.left;
        if (x == null) {
            return;
        }
        int xLeftCount = x.leftCount;
        int yLeftCount = y.leftCount;
        int xRightCount = yLeftCount - xLeftCount - 1;

        y.left = x.right;
        if (x.right != null) {
            x.right.parent = y;
        }

        x.parent = y.parent;
        if (y.parent == null) {
            root = x;
        } else if (y == y.parent.left) {
            y.parent.left = x;
        } else {
            y.parent.right = x;
        }

        x.right = y;
        y.parent = x;

        // y 从“共享旧基准”变为“以 x 为左父基准”，因此要减去 x.delta。
        y.delta -= x.delta;
        // 旋转后 y 的左子树变为 x 的右子树（旋转前）。
        y.leftCount = xRightCount;
    }

    private void fixAfterInsert(Node z) {
        while (z != root && colorOf(parentOf(z)) == RED) {
            if (parentOf(z) == leftOf(parentOf(parentOf(z)))) {
                Node y = rightOf(parentOf(parentOf(z)));
                if (colorOf(y) == RED) {
                    setColor(parentOf(z), BLACK);
                    setColor(y, BLACK);
                    setColor(parentOf(parentOf(z)), RED);
                    z = parentOf(parentOf(z));
                } else {
                    if (z == rightOf(parentOf(z))) {
                        z = parentOf(z);
                        rotateLeft(z);
                    }
                    setColor(parentOf(z), BLACK);
                    setColor(parentOf(parentOf(z)), RED);
                    rotateRight(parentOf(parentOf(z)));
                }
            } else {
                Node y = leftOf(parentOf(parentOf(z)));
                if (colorOf(y) == RED) {
                    setColor(parentOf(z), BLACK);
                    setColor(y, BLACK);
                    setColor(parentOf(parentOf(z)), RED);
                    z = parentOf(parentOf(z));
                } else {
                    if (z == leftOf(parentOf(z))) {
                        z = parentOf(z);
                        rotateRight(z);
                    }
                    setColor(parentOf(z), BLACK);
                    setColor(parentOf(parentOf(z)), RED);
                    rotateLeft(parentOf(parentOf(z)));
                }
            }
        }
        setColor(root, BLACK);
    }

    private static Node parentOf(Node n) {
        return n == null ? null : n.parent;
    }

    private static Node leftOf(Node n) {
        return n == null ? null : n.left;
    }

    private static Node rightOf(Node n) {
        return n == null ? null : n.right;
    }

    private static boolean colorOf(Node n) {
        return n == null ? BLACK : n.color;
    }

    private static void setColor(Node n, boolean color) {
        if (n != null) {
            n.color = color;
        }
    }
}
