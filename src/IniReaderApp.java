import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

public class IniReaderApp {
    public static void main(String[] args) {
        Path iniPath = args.length > 0 ? Paths.get(args[0]) : Paths.get("config", "sample.ini");

        try {
            IniData iniData = IniParser.parse(iniPath);
            System.out.println("已读取文件: " + iniPath.toAbsolutePath());
            System.out.println();

            for (String section : iniData.sections().keySet()) {
                String sectionName = section.isEmpty() ? "(global)" : section;
                System.out.println("[" + sectionName + "]");
                for (Map.Entry<String, String> entry : iniData.entries(section).entrySet()) {
                    System.out.println(entry.getKey() + " = " + entry.getValue());
                }
                System.out.println();
            }

            Optional<String> dbHost = iniData.get("database", "host");
            Optional<String> dbPort = iniData.get("database", "port");
            System.out.println("database.host -> " + dbHost.orElse("未配置"));
            System.out.println("database.port -> " + dbPort.orElse("未配置"));
        } catch (IOException e) {
            System.err.println("读取 ini 文件失败: " + e.getMessage());
            System.exit(1);
        }
    }

    static final class IniParser {
        private IniParser() {
        }

        static IniData parse(Path path) throws IOException {
            Map<String, Map<String, String>> parsed = new LinkedHashMap<>();
            String currentSection = "";
            parsed.put(currentSection, new LinkedHashMap<>());

            try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                String rawLine;
                int lineNumber = 0;
                while ((rawLine = reader.readLine()) != null) {
                    lineNumber++;
                    String line = rawLine.trim();

                    if (line.isEmpty() || line.startsWith(";") || line.startsWith("#")) {
                        continue;
                    }

                    if (line.startsWith("[") && line.endsWith("]")) {
                        String section = line.substring(1, line.length() - 1).trim();
                        if (section.isEmpty()) {
                            throw new IOException("第 " + lineNumber + " 行 section 名为空");
                        }
                        currentSection = section;
                        parsed.computeIfAbsent(currentSection, key -> new LinkedHashMap<>());
                        continue;
                    }

                    int delimiterIndex = findDelimiter(line);
                    if (delimiterIndex <= 0) {
                        throw new IOException("第 " + lineNumber + " 行格式非法: " + rawLine);
                    }

                    String key = line.substring(0, delimiterIndex).trim();
                    String value = line.substring(delimiterIndex + 1).trim();
                    if (key.isEmpty()) {
                        throw new IOException("第 " + lineNumber + " 行 key 为空");
                    }

                    parsed.computeIfAbsent(currentSection, k -> new LinkedHashMap<>()).put(key, value);
                }
            }

            return new IniData(parsed);
        }

        private static int findDelimiter(String line) {
            int equalsIndex = line.indexOf('=');
            int colonIndex = line.indexOf(':');

            if (equalsIndex < 0) {
                return colonIndex;
            }
            if (colonIndex < 0) {
                return equalsIndex;
            }
            return Math.min(equalsIndex, colonIndex);
        }
    }

    static final class IniData {
        private final Map<String, Map<String, String>> sections;

        IniData(Map<String, Map<String, String>> source) {
            Map<String, Map<String, String>> copy = new LinkedHashMap<>();
            for (Map.Entry<String, Map<String, String>> section : source.entrySet()) {
                copy.put(section.getKey(), new LinkedHashMap<>(section.getValue()));
            }
            this.sections = Collections.unmodifiableMap(copy);
        }

        Map<String, Map<String, String>> sections() {
            return sections;
        }

        Map<String, String> entries(String section) {
            return sections.getOrDefault(section, Collections.emptyMap());
        }

        Optional<String> get(String section, String key) {
            return Optional.ofNullable(sections.getOrDefault(section, Collections.emptyMap()).get(key));
        }
    }
}
