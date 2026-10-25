import com.reandroid.arsc.chunk.xml.AndroidManifestBlock;
import com.reandroid.arsc.chunk.xml.ResXmlElement;
import java.util.List;
import java.util.TreeSet;
import java.util.zip.ZipFile;

public final class FacebookProfileLinksTest {
    static AndroidManifestBlock load(String path) throws Exception {
        try (var apk = new ZipFile(path); var input = apk.getInputStream(apk.getEntry("AndroidManifest.xml"))) {
            return AndroidManifestBlock.load(input);
        }
    }

    static int count(ResXmlElement element, String tag) {
        int count = 0;
        var entries = element.getElements(tag);
        while (entries.hasNext()) { entries.next(); count++; }
        return count;
    }

    static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    static void refuses(AndroidManifestBlock manifest) {
        try { FacebookProfileLinks.patch(manifest); }
        catch (IllegalArgumentException expected) { return; }
        throw new AssertionError("Unsafe or duplicate patch was accepted");
    }

    public static void main(String[] args) throws Exception {
        var manifest = load(args[0]);
        var permissions = new TreeSet<>(manifest.getUsesPermissions());
        var target = FacebookProfileLinks.find(manifest, "activity", FacebookProfileLinks.HANDLER);
        int filters = count(target, "intent-filter");
        FacebookProfileLinks.patch(manifest);
        require(count(target, "intent-filter") == filters + 1, "Expected exactly one added filter");
        require(target.searchAttributeByResourceId(0x0101000e).getValueAsBoolean(), "Handler must default to enabled");
        require(new TreeSet<>(manifest.getUsesPermissions()).equals(permissions), "Permission declarations changed");
        require(!FacebookProfileLinks.find(manifest, "activity-alias", FacebookProfileLinks.SOURCE)
                .searchAttributeByResourceId(0x0101000e).getValueAsBoolean(), "Original alias changed");
        ResXmlElement added = null;
        var entries = target.getElements("intent-filter");
        while (entries.hasNext()) added = (ResXmlElement) entries.next();
        var hosts = new TreeSet<String>();
        var paths = new TreeSet<String>();
        var data = added.getElements("data");
        while (data.hasNext()) {
            var element = (ResXmlElement) data.next();
            var host = element.searchAttributeByResourceId(0x01010028);
            var path = element.searchAttributeByResourceId(0x01010620);
            if (host != null) hosts.add(host.getValueAsString());
            if (path != null) paths.add(path.getValueAsString());
        }
        require(hosts.containsAll(List.of("facebook.com", "www.facebook.com", "m.facebook.com")), "Missing canonical hosts");
        require(hosts.stream().allMatch(h -> h.equals("fb.com") || h.equals("facebook.com") || h.endsWith(".facebook.com")), "Unrelated domain claimed");
        require(paths.equals(new TreeSet<>(FacebookProfileLinks.PATHS)), "Unexpected path scope");
        var blocked = new TreeSet<String>();
        var groups = added.getElements("uri-relative-filter-group");
        while (groups.hasNext()) {
            var group = (ResXmlElement) groups.next();
            if (!group.searchAttributeByResourceId(0x0101068e).getValueAsBoolean()) {
                blocked.add(group.getElement("data").searchAttributeByResourceId(0x0101002a).getValueAsString());
            }
        }
        for (String path : FacebookProfileLinks.AUTH_PATHS) {
            require(blocked.contains(path) && blocked.contains(path + "/"), "Authentication exclusion missing: " + path);
        }
        for (String path : List.of("/zuck", "/100000000000000", "/zuck/", "/people/Example/100000000000000/")) {
            require(paths.stream().anyMatch(path::matches), "Profile path missing: " + path);
        }
        for (String path : List.of("/login/index.php", "/privacy/consent/appswitch", "/app/dialog/oauth")) {
            require(paths.stream().noneMatch(path::matches), "Authentication path captured: " + path);
        }
        refuses(manifest);
        var privateHandler = load(args[0]);
        FacebookProfileLinks.find(privateHandler, "activity", FacebookProfileLinks.HANDLER)
            .getOrCreateAndroidAttribute("exported", 0x01010010).setValueAsBoolean(false);
        refuses(privateHandler);
        var wrongPackage = load(args[0]);
        wrongPackage.getManifestElement().searchAttributeByName("package").setValueAsString("other.app");
        refuses(wrongPackage);
        var missingAlias = load(args[0]);
        FacebookProfileLinks.find(missingAlias, "activity-alias", FacebookProfileLinks.SOURCE)
            .getOrCreateAndroidAttribute("name", 0x01010003).setValueAsString("other.Alias");
        refuses(missingAlias);
        System.out.println("PASS: Facebook profile manifest, scope, permissions, and fail-closed checks");
    }
}
