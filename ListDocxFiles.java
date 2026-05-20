import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

public class ListDocxFiles {
    public static void main(String[] args) {
        Path directory = args.length > 0 ? Paths.get(args[0]) : Paths.get(".");

        if (!Files.exists(directory)) {
            System.err.println("目录不存在: " + directory.toAbsolutePath());
            System.exit(1);
        }

        if (!Files.isDirectory(directory)) {
            System.err.println("不是有效目录: " + directory.toAbsolutePath());
            System.exit(1);
        }

        try (DirectoryStream<Path> stream = Files.newDirectoryStream(directory)) {
            boolean found = false;
            for (Path file : stream) {
                if (Files.isRegularFile(file)
                        && file.getFileName().toString().toLowerCase().endsWith(".docx")) {
                    System.out.println(file.getFileName());
                    found = true;
                }
            }

            if (!found) {
                System.out.println("未找到 .docx 文件");
            }
        } catch (IOException e) {
            System.err.println("读取目录失败: " + e.getMessage());
            System.exit(1);
        }
    }
}
