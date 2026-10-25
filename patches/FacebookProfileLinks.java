import com.reandroid.arsc.chunk.xml.AndroidManifestBlock;
import com.reandroid.arsc.chunk.xml.ResXmlElement;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.TreeSet;
import java.util.zip.ZipFile;

public final class FacebookProfileLinks {
    static final String PACKAGE = "com.facebook.katana";
    static final String HANDLER = PACKAGE + ".IntentUriHandler";
    static final String SOURCE = "com.facebook.deeplinking.aliasactivity.CatchAllUriAliasActivity";
    static final List<String> PATHS = List.of("/[^/]+", "/[^/]+/", "/people/[^/]+/[0-9]+", "/people/[^/]+/[0-9]+/");
    static final List<String> AUTH_PATHS = List.of("/login", "/login.php", "/logout", "/logout.php", "/checkpoint", "/consent", "/dialog", "/oauth");

    static ResXmlElement find(AndroidManifestBlock manifest, String tag, String name) {
        ResXmlElement result = null;
        var entries = manifest.getApplicationElement().getElements(tag);
        while (entries.hasNext()) {
            var element = (ResXmlElement) entries.next();
            if (name.equals(manifest.fullClassName(AndroidManifestBlock.getAndroidNameValue(element)))) {
                if (result != null) throw new IllegalArgumentException("Duplicate component: " + name);
                result = element;
            }
        }
        if (result == null) throw new IllegalArgumentException("Missing component: " + name);
        return result;
    }

    static void attribute(ResXmlElement element, String name, int id, String value) {
        element.getOrCreateAndroidAttribute(name, id).setValueAsString(value);
    }

    static void named(ResXmlElement parent, String tag, String name) {
        attribute(parent.newElement(tag), "name", 0x01010003, name);
    }

    static void patch(AndroidManifestBlock manifest) {
        if (!PACKAGE.equals(manifest.getPackageName())) throw new IllegalArgumentException("Expected Facebook package");
        var target = find(manifest, "activity", HANDLER);
        var exported = target.searchAttributeByResourceId(0x01010010);
        if (exported == null || !exported.getValueAsBoolean() || target.searchAttributeByResourceId(0x01010006) != null) {
            throw new IllegalArgumentException("Expected a public URL handler without a permission gate");
        }
        var hosts = new TreeSet<String>();
        var sourceFilters = find(manifest, "activity-alias", SOURCE).getElements("intent-filter");
        while (sourceFilters.hasNext()) {
            var data = ((ResXmlElement) sourceFilters.next()).getElements("data");
            while (data.hasNext()) {
                var host = ((ResXmlElement) data.next()).searchAttributeByResourceId(0x01010028);
                if (host == null) continue;
                String value = host.getValueAsString();
                if (value.equals("facebook.com") || value.endsWith(".facebook.com") || value.equals("fb.com")) hosts.add(value);
            }
        }
        if (!hosts.containsAll(List.of("facebook.com", "www.facebook.com", "m.facebook.com"))) {
            throw new IllegalArgumentException("Expected Facebook profile domains in original alias");
        }
        var filters = target.getElements("intent-filter");
        while (filters.hasNext()) {
            var data = ((ResXmlElement) filters.next()).getElements("data");
            while (data.hasNext()) {
                var path = ((ResXmlElement) data.next()).searchAttributeByResourceId(0x01010620);
                if (path != null && PATHS.get(2).equals(path.getValueAsString())) {
                    throw new IllegalArgumentException("Profile-link filter is already present");
                }
            }
        }
        target.getOrCreateAndroidAttribute("enabled", 0x0101000e).setValueAsBoolean(true);
        var filter = target.newElement("intent-filter");
        filter.getOrCreateAndroidAttribute("autoVerify", 0x010104ee).setValueAsBoolean(true);
        named(filter, "action", "android.intent.action.VIEW");
        named(filter, "category", "android.intent.category.DEFAULT");
        named(filter, "category", "android.intent.category.BROWSABLE");
        for (String scheme : List.of("http", "https")) attribute(filter.newElement("data"), "scheme", 0x01010027, scheme);
        for (String host : hosts) attribute(filter.newElement("data"), "host", 0x01010028, host);
        for (String path : PATHS) attribute(filter.newElement("data"), "pathAdvancedPattern", 0x01010620, path);
        for (String path : AUTH_PATHS) {
            for (String suffix : List.of("", "/")) {
                var excluded = filter.newElement("uri-relative-filter-group");
                excluded.getOrCreateAndroidAttribute("allow", 0x0101068e).setValueAsBoolean(false);
                attribute(excluded.newElement("data"), "path", 0x0101002a, path + suffix);
            }
        }
        var allowed = filter.newElement("uri-relative-filter-group");
        allowed.getOrCreateAndroidAttribute("allow", 0x0101068e).setValueAsBoolean(true);
        attribute(allowed.newElement("data"), "pathPattern", 0x0101002c, "/.*");
        manifest.refreshFull();
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("Expected input APK and output binary manifest");
        try (var apk = new ZipFile(args[0]); var input = apk.getInputStream(apk.getEntry("AndroidManifest.xml"))) {
            var manifest = AndroidManifestBlock.load(input);
            patch(manifest);
            Files.write(Path.of(args[1]), manifest.getBytes());
        }
    }
}
