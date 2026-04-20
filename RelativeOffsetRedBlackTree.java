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

    private static final class Node {
        long delta; // key = leftParentAbsoluteKey + delta
        boolean color;
        Node left;
        Node right;
        Node parent;

        Node(long delta, boolean color) {
            this.delta = delta;
            this.color = color;
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

        while (cur != null) {
            parent = cur;
            long curKey = base + cur.delta;
            if (key < curKey) {
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

        Long firstAffected = lowerBound(fromInclusive);
        if (firstAffected == null) {
            return;
        }
        Long predecessor = predecessorOf(firstAffected);
        if (predecessor != null && firstAffected + delta <= predecessor) {
            throw new IllegalArgumentException(
                    "Shift would break BST order: firstAffected=" + firstAffected
                            + ", predecessor=" + predecessor
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

    private Long lowerBound(long target) {
        Node cur = root;
        long base = 0L;
        Long candidate = null;
        while (cur != null) {
            long key = base + cur.delta;
            if (key >= target) {
                candidate = key;
                cur = cur.left;
            } else {
                base = key;
                cur = cur.right;
            }
        }
        return candidate;
    }

    private Long predecessorOf(long key) {
        Node cur = root;
        long base = 0L;
        Long predecessor = null;
        while (cur != null) {
            long curKey = base + cur.delta;
            if (curKey < key) {
                predecessor = curKey;
                base = curKey;
                cur = cur.right;
            } else {
                cur = cur.left;
            }
        }
        return predecessor;
    }

    private void rotateLeft(Node x) {
        Node y = x.right;
        if (y == null) {
            return;
        }

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
    }

    private void rotateRight(Node y) {
        Node x = y.left;
        if (x == null) {
            return;
        }

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
