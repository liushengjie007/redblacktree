import java.io.IOException;
import java.io.InputStream;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NamedNodeMap;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.SAXException;

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
                    parseDocxFontTable(file);
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

    private static void parseDocxFontTable(Path docxPath) {
        try (ZipFile zipFile = new ZipFile(docxPath.toFile())) {
            ZipEntry fontTableEntry = zipFile.getEntry("word/fontTable.xml");
            if (fontTableEntry == null) {
                System.err.println("跳过 " + docxPath.getFileName() + "：缺少 word/fontTable.xml");
                return;
            }

            try (InputStream inputStream = zipFile.getInputStream(fontTableEntry)) {
                Document document = parseXml(inputStream);
                Element root = document.getDocumentElement();
                if (root == null || !"fonts".equals(root.getLocalName())) {
                    System.err.println("跳过 " + docxPath.getFileName() + "：fontTable.xml 根节点不是 w:fonts");
                    return;
                }

                NodeList nodes = root.getChildNodes();
                for (int i = 0; i < nodes.getLength(); i++) {
                    Node node = nodes.item(i);
                    if (node.getNodeType() == Node.ELEMENT_NODE
                            && "font".equals(node.getLocalName())) {
                        StringBuilder builder = new StringBuilder();
                        appendElementAndChildren((Element) node, builder);
                        System.out.println(builder.toString().replace("\r", "").replace("\n", ""));
                    }
                }
            }
        } catch (IOException | ParserConfigurationException | SAXException e) {
            System.err.println("解析失败 " + docxPath.getFileName() + "：" + e.getMessage());
        }
    }

    private static Document parseXml(InputStream inputStream)
            throws ParserConfigurationException, IOException, SAXException {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        DocumentBuilder builder = factory.newDocumentBuilder();
        return builder.parse(inputStream);
    }

    private static void appendElementAndChildren(Element element, StringBuilder builder) {
        if (builder.length() > 0) {
            builder.append(' ');
        }
        builder.append(element.getNodeName());
        builder.append('(');

        NamedNodeMap attributes = element.getAttributes();
        boolean firstAttribute = true;
        for (int i = 0; i < attributes.getLength(); i++) {
            Node attribute = attributes.item(i);
            String name = attribute.getNodeName();
            if (name.startsWith("xmlns")) {
                continue;
            }
            if (!firstAttribute) {
                builder.append(',');
            }
            builder.append(attribute.getNodeValue());
            firstAttribute = false;
        }
        builder.append(')');

        NodeList children = element.getChildNodes();
        for (int i = 0; i < children.getLength(); i++) {
            Node child = children.item(i);
            if (child.getNodeType() == Node.ELEMENT_NODE) {
                appendElementAndChildren((Element) child, builder);
            }
        }
    }
}
