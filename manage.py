"""Small, non-root Android reconciler. Public commands live in justfile."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).strip())
    return result.stdout.strip()


def confirm(message, yolo):
    # A safe default also applies to unattended runs; YOLO never implies data loss.
    if yolo or not sys.stdin.isatty():
        return False
    return input(f"{message} [y/N] ").strip().lower() in {"y", "yes"}


def checksum(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(name, app, suffix=".apk"):
    url = urllib.parse.urlparse(app["url"])
    github = url.netloc == "github.com" and re.fullmatch(r"/[^/]+/[^/]+/releases/download/[^/]+/[^/]+", url.path)
    fdroid = url.netloc == "f-droid.org" and re.fullmatch(r"/repo/[A-Za-z][A-Za-z0-9_.]+_\d+\.apk", url.path)
    if url.scheme != "https" or not (github or fdroid) or url.query or url.fragment:
        raise ValueError("Artifact URL must be a pinned HTTPS GitHub release or F-Droid APK")
    expected = app["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("APK must have a pinned SHA-256 digest")
    cache = ROOT / ".cache" / "apks"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{expected}{suffix}"
    if target.exists():
        if checksum(target) == expected:
            return target
        target.unlink()
    print(f"Download {name} {app['version']}", flush=True)
    fd, temporary = tempfile.mkstemp(dir=cache, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(app["url"], timeout=60) as source:
            digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"Checksum mismatch for {name}; refusing installation")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def java():
    candidates = [Path(os.environ.get("JAVA_HOME", "")) / "bin/java",
                  Path("/opt/homebrew/opt/openjdk@21/bin/java"),
                  Path(shutil.which("java") or "/missing")]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise RuntimeError("Install Java 21+; on macOS: brew install openjdk@21")


def sdktool(name):
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or Path.home() / "Library/Android/sdk")
    versions = sorted((sdk / "build-tools").glob("*"),
                      key=lambda p: tuple(int(v) for v in re.findall(r"\d+", p.name)), reverse=True)
    for version in versions:
        if (version / name).is_file():
            return str(version / name)
    raise RuntimeError("Install Android SDK Build Tools and set ANDROID_HOME to build patched apps")


def apk_identity(path):
    # Read manifest attributes directly; badging resolves icons absent in older SDK tools.
    manifest = run(sdktool("aapt"), "dump", "xmltree", str(path), "AndroidManifest.xml")
    root = re.search(r"(?m)^([ \t]*)E: manifest\b", manifest)
    if not root:
        raise RuntimeError(f"Cannot read APK manifest: {path}")
    attributes = manifest[root.end():].split("\n" + root[1] + "  E:", 1)[0]
    package = re.search(r'A: package="([^"\n]+)"', attributes)
    code = re.search(r'A: \w+:versionCode\(0x0101021b\)=\(type 0x10\)(0x[0-9a-fA-F]+)', attributes)
    version = re.search(r'A: \w+:versionName\(0x0101021c\)="([^"\n]+)"', attributes)
    if not all((package, code, version)):
        raise RuntimeError(f"Cannot read APK package/version: {path}")
    return package[1], int(code[1], 16), version[1]


def capture(device, app, source):
    package = app["build"]["device"]
    if device.version(package) != app["build"].get("code", app["code"]):
        raise RuntimeError(f"{package} on {device.name} is not the pinned original version")
    source.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=source.parent) as temporary:
        paths = device.shell("pm", "path", "--user", "0", package).splitlines()
        if not paths or any(not p.startswith("package:") for p in paths):
            raise RuntimeError(f"Cannot locate original APKs for {package}")
        for remote in paths:
            path = remote.removeprefix("package:")
            device.adb("pull", path, str(Path(temporary) / Path(path).name))
        bundle = Path(temporary) / "original.apkm"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as archive:
            for path in sorted(Path(temporary).glob("*.apk")):
                # Fixed ZIP timestamps let another capture match the reviewed input digest.
                archive.writestr(path.name, path.read_bytes())
        if checksum(bundle) != app["build"]["sha256"]:
            raise RuntimeError("Captured original APK set differs from the pinned input; refusing to patch")
        bundle.replace(source)


def patch_options(patches, required=False):
    resolved = []
    for patch in patches:
        if "options_file" not in patch:
            resolved.append(patch)
            continue
        path = ROOT / patch["options_file"]
        if not path.is_file():
            if required:
                raise RuntimeError(f"Provide private patch options in {path}; see docs/setup.md")
            return None
        options = json.loads(path.read_text())
        if not isinstance(options, dict) or not options or any(
                not isinstance(k, str) or not isinstance(v, str) or not v or v != v.strip()
                for k, v in options.items()):
            raise ValueError(f"{path} must contain nonempty string patch options without surrounding whitespace")
        resolved.append(dict(patch, options=dict(patch.get("options", {}), **options)))
    return resolved


def facebook_profile_links(apk, cli, key):
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        manifest, unsigned, aligned = (folder / name for name in ("manifest.bin", "unsigned.apk", "aligned.apk"))
        run(str(java()), "--class-path", str(cli), str(ROOT / "patches/FacebookProfileLinks.java"), str(apk), str(manifest))
        with zipfile.ZipFile(apk) as source, zipfile.ZipFile(unsigned, "w") as target:
            if source.namelist().count("AndroidManifest.xml") != 1:
                raise RuntimeError("Expected exactly one Facebook manifest")
            for entry in source.infolist():
                if entry.filename.upper() == "META-INF/MANIFEST.MF" or re.fullmatch(r"META-INF/[^/]+\.(SF|RSA|DSA|EC)", entry.filename, re.I):
                    continue
                data = manifest.read_bytes() if entry.filename == "AndroidManifest.xml" else source.read(entry)
                target.writestr(entry, data)
        run(sdktool("zipalign"), "-P", "16", "-f", "4", str(unsigned), str(aligned))
        run("env", f"JAVA_HOME={java().parent.parent}", sdktool("apksigner"), "sign", "--ks", str(key),
            "--ks-pass", "pass:Morphe", "--key-pass", "pass:Morphe", "--ks-key-alias", "morphe", "--out", str(apk), str(aligned))
        run(sdktool("zipalign"), "-c", "-P", "16", "4", str(apk))


def artifact(name, app, create=False, device=None):
    """Resolve a cached build, or build exactly the declared inputs when requested."""
    if app.get("native"):
        return home_artifact(app, create)
    if "file" in app:
        return dict(app, path=str(ROOT / ".local/inputs" / app["file"]))
    if "build" not in app:
        return app
    tools = read("builds.json")
    private = ROOT / ".local"
    key = private / "morphe.keystore"
    if create:
        private.mkdir(mode=0o700, exist_ok=True)
        private.chmod(0o700)
        if not key.exists():
            if any((ROOT / ".cache/builds").glob("*/artifact.json")):
                raise RuntimeError("Signing key is missing; restore .local/morphe.keystore from backup")
            print("Create persistent local signing key; back up .local/morphe.keystore", flush=True)
            run(str(java().with_name("keytool")), "-genkeypair", "-alias", "Morphe", "-keyalg", "RSA",
                "-keysize", "2048", "-validity", "10000", "-dname", "CN=Andromeda", "-storetype", "PKCS12",
                "-keystore", str(key), "-storepass", "Morphe", "-keypass", "Morphe")
        key.chmod(0o600)
    if not key.exists():
        return dict(app, sha256=None)
    spec = app["build"]
    enabled_patches = patch_options(spec.get("enable", []), required=create)
    if enabled_patches is None:
        return dict(app, sha256=None)
    selected_tools = {name: tools[name] for name in ("desktop", spec["patches"])}
    build_app = {key: value for key, value in app.items() if key != "activity"}
    profile_base = None
    if spec.get("profile_links"):
        if app["package"] != "com.facebook.katana":
            raise ValueError("The profile-links patch supports Facebook only")
        base_spec = {key: value for key, value in spec.items() if key != "profile_links"}
        profile_base = artifact(name, dict(app, build=base_spec), create=create, device=device)
        if not profile_base.get("sha256"):
            return dict(app, sha256=None)
        build_app["profile_links_base"] = profile_base["sha256"]
        build_app["profile_links_source"] = checksum(ROOT / "patches/FacebookProfileLinks.java")
    if "enable" in spec:
        build_app["build"] = dict(spec, enable=enabled_patches)
    fingerprint = hashlib.sha256(json.dumps({"app": build_app, "tools": selected_tools, "key": checksum(key),
                                            "mode": "FULL", "abi": "arm64-v8a", "alias": "morphe"},
                                            sort_keys=True).encode()).hexdigest()
    folder = ROOT / ".cache/builds" / fingerprint
    output = folder / "app.apk"
    receipt = folder / "artifact.json"
    if output.exists() and receipt.exists():
        expected = json.loads(receipt.read_text())["sha256"]
        if checksum(output) == expected:
            return dict(app, path=str(output), sha256=expected)
    if not create:
        return dict(app, sha256=None)
    log = folder / "build.log"
    if profile_base:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(profile_base["path"], output)
        cli = download("Morphe CLI", tools["desktop"], ".jar")
        facebook_profile_links(output, cli, key)
    else:
        source = private / "inputs" / spec["file"]
        if not source.is_file() and device and spec.get("device"):
            capture(device, app, source)
        if not source.is_file():
            raise RuntimeError(f"Provide the original {app['version']} APK at {source}; see {spec['source']}")
        if checksum(source) != spec["sha256"]:
            raise RuntimeError(f"Original {name} checksum does not match apps.json")
        folder.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, JAVA_HOME=str(java().parent.parent), MORPHE_DATA_DIR=str(private / "morphe"))
        with tempfile.TemporaryDirectory() as temporary:
            if source.suffix == ".apkm":
                with zipfile.ZipFile(source) as archive:
                    originals = []
                    for index, entry in enumerate(archive.namelist()):
                        if entry.endswith(".apk"):
                            path = Path(temporary) / f"{index}.apk"
                            path.write_bytes(archive.read(entry))
                            originals.append(path)
                    if not originals:
                        raise RuntimeError("Original bundle contains no APKs")
            else:
                originals = [source]
            for original in originals:
                result = subprocess.run([sdktool("apksigner"), "verify", "--print-certs", str(original)],
                                        env=environment, text=True, capture_output=True, check=True)
                certs = re.findall(r"certificate SHA-256 digest: ([a-f0-9]+)", result.stdout)
                if spec["certificate"] not in certs:
                    raise RuntimeError(f"Original {name} is not signed by the declared publisher")
        cli = download("Morphe CLI", tools["desktop"], ".jar")
        patches = download(spec["patches"], tools[spec["patches"]], ".mpp")
        command = [str(java()), "-Xmx4g", "-jar", str(cli), "patch", "-p", str(patches),
                   "--keystore", str(key), "--keystore-password", "Morphe", "--keystore-entry-password", "Morphe",
                   "--keystore-entry-alias", "morphe",
                   "--striplibs", "arm64-v8a", "--bytecode-mode", "FULL", "-o", str(output),
                   "-t", str(folder / "tmp"), "-r", str(folder / "report.json")]
        for enabled in enabled_patches:
            command += ["-e", enabled["name"]]
            for option, value in enabled.get("options", {}).items():
                command += ["-O", f"{option}={value}"]
        command.append(str(source))
        log = folder / "build.log"
        print(f"Build {name} {app['version']}; log: {log}", flush=True)
        with log.open("w") as stream:
            result = subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        report = folder / "report.json"
        if result.returncode or not output.exists() or not report.exists() or not json.loads(report.read_text()).get("success"):
            raise RuntimeError(f"Building {name} failed; see {log}")
    identity = apk_identity(output)
    if identity != (app["package"], app["code"], app["version"]):
        raise RuntimeError(f"Built {name} has unexpected package/version: {identity}; see {log}")
    subprocess.run([sdktool("apksigner"), "verify", str(output)], env=dict(os.environ, JAVA_HOME=str(java().parent.parent)),
                   capture_output=True, check=True)
    expected = checksum(output)
    receipt.write_text(json.dumps({"sha256": expected}, indent=2) + "\n")
    return dict(app, path=str(output), sha256=expected)


def home_artifact(app, create=False):
    source = ROOT / "home"
    files = sorted(p for p in source.rglob("*") if p.suffix in {".java", ".xml"})
    key = ROOT / ".local/morphe.keystore"
    if not key.exists():
        if create:
            raise RuntimeError("Restore .local/morphe.keystore or build a patched app before building Home")
        return dict(app, sha256=None)
    identity = checksum(key) + "".join(str(p.relative_to(source)) + checksum(p) for p in files)
    folder = ROOT / ".cache/home" / hashlib.sha256(identity.encode()).hexdigest()
    output, receipt = folder / "app.apk", folder / "artifact.json"
    if output.exists() and receipt.exists() and checksum(output) == json.loads(receipt.read_text())["sha256"]:
        return dict(app, path=str(output), sha256=checksum(output))
    if not create:
        return dict(app, sha256=None)
    sdk = Path(sdktool("aapt")).parents[2]
    platform = sdk / "platforms/android-37.0/android.jar"
    if not platform.is_file():
        raise RuntimeError("Install Android SDK Platform 37 to build Andromeda Home")
    folder.mkdir(parents=True, exist_ok=True)
    classes, dex = folder / "classes", folder / "dex"
    classes.mkdir(exist_ok=True)
    dex.mkdir(exist_ok=True)
    run(sdktool("aapt"), "package", "-f", "-M", str(source / "AndroidManifest.xml"),
        "-S", str(source / "res"), "-I", str(platform), "-F", str(folder / "resources.apk"))
    run(str(java().with_name("javac")), "--release", "17", "-cp", str(platform), "-d", str(classes),
        *map(str, source.glob("*.java")))
    run("env", f"JAVA_HOME={java().parent.parent}", sdktool("d8"), "--min-api", "30",
        "--lib", str(platform), "--output", str(dex), *map(str, classes.rglob("*.class")))
    unsigned = folder / "unsigned.apk"
    shutil.copyfile(folder / "resources.apk", unsigned)
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.write(dex / "classes.dex", "classes.dex")
    run(sdktool("zipalign"), "-f", "4", str(unsigned), str(output))
    run("env", f"JAVA_HOME={java().parent.parent}", sdktool("apksigner"), "sign", "--ks", str(key),
        "--ks-pass", "pass:Morphe", "--key-pass", "pass:Morphe", "--ks-key-alias", "morphe", str(output))
    run("env", f"JAVA_HOME={java().parent.parent}", sdktool("apksigner"), "verify", str(output))
    if apk_identity(output) != (app["package"], app["code"], app["version"]):
        raise RuntimeError("Andromeda Home build has an unexpected package/version")
    expected = checksum(output)
    receipt.write_text(json.dumps({"sha256": expected}) + "\n")
    return dict(app, path=str(output), sha256=expected)


def owner_permissions(details):
    shared = re.search(r"(?m)^\s*sharedUser=SharedUserSetting\{\S+ ([A-Za-z][A-Za-z0-9_.]+)/\d+\}", details.split("Hidden system packages:", 1)[0])
    if shared:
        # Shared-UID runtime grants are dumped separately from the package's User 0 block.
        group = re.search(r"(?ms)^  SharedUser \[" + re.escape(shared[1]) + r"\].*?(?=^  SharedUser |^\S|\Z)",
                          details.split("Shared users:\n", 1)[-1])
        if not group:
            raise RuntimeError("Cannot read shared-UID permission state")
        details = group[0]
    owner = re.search(r"(?ms)^\s*User 0:.*?(?=^\s*User \d+:|^\S|\Z)", details)
    if not owner:
        raise RuntimeError("Cannot read owner-profile permissions")
    runtime = owner[0].split("runtime permissions:", 1)
    return {name: (granted == "true", flags) for name, granted, flags in
            re.findall(r"(android\.permission\.[A-Z0-9_]+): granted=(true|false), flags=\[([^\]]*)\]",
                       runtime[1] if len(runtime) == 2 else "")}


def owner_app_links(details):
    owner = re.search(r"(?ms)^\s*User 0:.*?(?=^\s*User \d+:|^\S|\Z)", details)
    if not owner:
        raise RuntimeError("Cannot read owner-profile app links")
    allowed = re.search(r"Verification link handling allowed: (true|false)", owner[0])
    if not allowed or "Selection state:" not in owner[0]:
        raise RuntimeError("Cannot read app link selection state")
    enabled, declared, section = set(), set(), None
    for line in owner[0].split("Selection state:", 1)[1].splitlines():
        value = line.strip()
        if value in {"Enabled:", "Disabled:"}:
            section = value
        elif value and section:
            declared.add(value)
            if section == "Enabled:":
                enabled.add(value)
    return allowed[1] == "true", enabled, declared


class Device:
    def __init__(self, name):
        profiles = read("devices.json")
        if name not in profiles:
            raise ValueError(f"Unknown device {name!r}; choose from {', '.join(profiles)}")
        self.name = name
        self.profile = profiles[name]
        bindings = read("devices.local.json") if (ROOT / "devices.local.json").exists() else {}
        self.binding = bindings.get(name, {})
        self.serial = self.binding.get("serial")
        if not self.serial:
            raise ValueError(f"Bind {name} in devices.local.json; see docs/setup.md")
        self.transport = self.binding.get("transport", self.serial)

    def adb(self, *args):
        return run("adb", "-s", self.transport, *args)

    def shell(self, *args):
        # adb shell joins arguments remotely; quote each value for that second shell.
        return self.adb("shell", shlex.join(args))

    def verify(self):
        if self.adb("get-state") != "device":
            raise RuntimeError("Device is offline; unlock it and authorize USB debugging")
        actual = self.shell("getprop", "ro.serialno")
        if actual != self.serial:
            raise RuntimeError(f"Identity mismatch for {self.name}; refusing to change this device")
        for prop, expected in (("ro.product.model", self.profile["model"]),
                               ("ro.product.cpu.abi", self.profile["abi"])):
            if self.shell("getprop", prop) != expected:
                raise RuntimeError(f"{self.name}: {prop} does not match {expected!r}")
        if self.shell("am", "get-current-user") != "0":
            raise RuntimeError("Switch to the owner profile (user 0) before managing this device")

    def version(self, package):
        packages = self.shell("pm", "list", "packages", "--user", "0", package).splitlines()
        if f"package:{package}" not in packages:
            return None
        details = self.shell("dumpsys", "package", package)
        match = re.search(r"\bversionCode=(\d+)", details)
        if not match:
            raise RuntimeError(f"Cannot read installed version of {package}")
        return int(match[1])

    def installed_checksum(self, package):
        paths = self.shell("pm", "path", "--user", "0", package).splitlines()
        if len(paths) != 1 or not paths[0].startswith("package:"):
            return None
        return self.shell("sha256sum", paths[0].removeprefix("package:")).split()[0]

    def wants_notifications(self, app):
        policy = self.profile.get("privacy")
        return app["package"] in policy["notifications"] if policy else app["notifications"]

    def install_apk(self, apk, app, replace=True):
        options = ["-r"] if replace else []
        if apk.suffix != ".apkm":
            return self.adb("install", *options, str(apk))
        with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(apk) as archive:
            entries = [name for name in archive.namelist() if name.endswith(".apk")]
            if entries.count("base.apk") != 1:
                raise ValueError("APK bundle must contain exactly one base.apk")
            paths = []
            for index, name in enumerate(entries):
                path = Path(temporary) / f"{index}.apk"
                path.write_bytes(archive.read(name))
                if name == "base.apk" and apk_identity(path) != (app["package"], app["code"], app["version"]):
                    raise ValueError("APK bundle has an unexpected package/version")
                result = run("env", f"JAVA_HOME={java().parent.parent}", sdktool("apksigner"),
                             "verify", "--print-certs", str(path))
                certs = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([a-f0-9]+)", result)
                if app["certificate"] not in certs:
                    raise ValueError("APK bundle is not signed by the declared publisher")
                paths.append(str(path))
            return self.adb("install-multiple", *options, *paths)

    def home_configuration(self):
        layout = self.profile["home"]
        apps = read("apps.json")
        def entry(name):
            if name not in apps and name not in layout["system"]:
                raise ValueError(f"Unknown home app {name!r}; declare it in apps.json or home.system")
            spec = apps.get(name) or layout["system"][name]
            package = spec["package"]
            activity = spec.get("activity")
            if not activity:
                resolved = self.shell("cmd", "package", "resolve-activity", "--brief", "--user", "0",
                                      "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package)
                activity = resolved.splitlines()[-1] if resolved else ""
            if not activity.startswith(package + "/"):
                raise ValueError(f"No launcher activity for home app {name} ({package})")
            return {"label": layout["labels"].get(name, name.title()), "component": activity}
        rows, dock = layout["rows"], layout["dock"]
        if not 1 <= len(rows) <= 8 or any(not 1 <= len(row) <= 4 for row in rows) or len(dock) != 4:
            raise ValueError("Home requires 1–8 rows of 1–4 apps and exactly four dock apps")
        return {"rows": [[entry(name) for name in row] for row in rows],
                "dock": [entry(name) for name in dock], "hidden": layout.get("hidden", [])}

    def home_drift(self):
        app = artifact("home", read("apps.json")["home"])
        changes = self.drift(app)
        if self.version(app["package"]) is None:
            return changes
        wanted = base64.b64encode(json.dumps(self.home_configuration(), sort_keys=True).encode()).decode()
        current = self.shell("content", "call", "--user", "0", "--uri", "content://us.andromeda.home.config", "--method", "get")
        if f"configuration={wanted}" not in current:
            changes.append("home layout differs")
        home = self.shell("cmd", "package", "resolve-activity", "--brief", "--user", "0",
                          "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
        if not home or not home.splitlines()[-1].startswith("us.andromeda.home/"):
            changes.append("select Andromeda Home")
        return changes

    def reconcile_home(self, yolo):
        layout = self.profile["home"]
        apps = read("apps.json")
        # Preloaded Google apps can be restored for user 0 without downloading APKs.
        for name in {name for row in layout["rows"] + [layout["dock"]] for name in row}:
            if name in layout["system"] and name not in apps:
                package = layout["system"][name]["package"]
                if self.version(package) is None:
                    self.shell("cmd", "package", "install-existing", "--user", "0", package)
                disabled = self.shell("pm", "list", "packages", "--user", "0", "-d", package).splitlines()
                if f"package:{package}" in disabled:
                    self.shell("pm", "enable", "--user", "0", package)
        config = self.home_configuration()
        app = artifact("home", apps["home"], create=True)
        self.reconcile("home", app, yolo)
        changes = self.home_drift()
        if "home layout differs" in changes:
            encoded = base64.b64encode(json.dumps(config, sort_keys=True).encode()).decode()
            self.shell("content", "call", "--user", "0", "--uri", "content://us.andromeda.home.config",
                       "--method", "configure", "--arg", encoded)
        if "select Andromeda Home" in changes:
            backup = ROOT / ".local/backups/home.json"
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                previous = self.shell("cmd", "package", "resolve-activity", "--brief", "--user", "0",
                                      "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
                backup.write_text(json.dumps({"previous": previous.splitlines()[-1]}, indent=2) + "\n")
            self.shell("cmd", "package", "set-home-activity", "--user", "0", "us.andromeda.home/.HomeActivity")
        remaining = self.home_drift()
        if remaining:
            raise RuntimeError("Home still differs: " + ", ".join(remaining))
        print("home: configured grid and dock are in sync")

    def stock_duplicates(self):
        apps = read("apps.json")
        managed = {app["package"] for app in apps.values()}
        for name, package in self.profile["remove_stock"].items():
            if (name not in apps or "build" not in apps[name] or package in managed
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]+", package)):
                raise ValueError(f"Invalid stock removal mapping: {name} -> {package}")
        system = self.shell("pm", "list", "packages", "--user", "0", "-s").splitlines()
        disabled = self.shell("pm", "list", "packages", "--user", "0", "-d").splitlines()
        duplicates = {}
        for name, package in self.profile["remove_stock"].items():
            entry = f"package:{package}"
            if self.version(package) is not None and not (entry in system and entry in disabled):
                duplicates[name] = package
        return duplicates

    def reconcile_stock_duplicates(self):
        duplicates = self.stock_duplicates()
        apps = read("apps.json")
        # Verify every replacement and retained input before deleting any stock app data.
        for name in duplicates:
            app = apps[name]
            source = ROOT / ".local/inputs" / app["build"]["file"]
            if (not (ROOT / ".local/morphe.keystore").is_file() or not source.is_file()
                    or checksum(source) != app["build"]["sha256"]):
                raise RuntimeError(f"Preserved stock {name}: retain its verified source APK and signing key first")
            if self.drift(artifact(name, app)):
                raise RuntimeError(f"Preserved stock {name}: patched replacement is not in sync")
        if duplicates:
            system = self.shell("pm", "list", "packages", "--user", "0", "-s").splitlines()
        for name, package in duplicates.items():
            if f"package:{package}" in system:
                # Android 17 blocks per-user system-app uninstall without root.
                self.shell("pm", "disable-user", "--user", "0", package)
                disabled = self.shell("pm", "list", "packages", "--user", "0", "-d", package).splitlines()
                if f"package:{package}" not in disabled:
                    raise RuntimeError(f"Could not disable stock {name}")
                print(f"Disabled preloaded stock {name} ({package}) for user 0")
            else:
                result = self.shell("pm", "uninstall", "--user", "0", package)
                if result != "Success" or self.version(package) is not None:
                    raise RuntimeError(f"Could not remove stock {name}: {result}")
                print(f"Removed stock {name} ({package}) for user 0")
        print("stock duplicates: in sync")

    def link_changes(self):
        apps = read("apps.json")
        owners, changes = {}, []
        for name, domains in self.profile["links"].items():
            if name not in apps or "build" not in apps[name] or not isinstance(domains, list) or not domains:
                raise ValueError(f"Declare a patched app and supported domains for links: {name}")
            package = apps[name]["package"]
            allowed, enabled, declared = owner_app_links(self.shell("pm", "get-app-links", "--user", "0", package))
            for domain in domains:
                if domain not in declared:
                    raise ValueError(f"{name} does not declare link domain {domain!r}")
                if domain in owners and owners[domain] != name:
                    raise ValueError(f"Conflicting link owners for {domain}: {owners[domain]}, {name}")
                owners[domain] = name
            if not allowed:
                changes.append((package, "allow", []))
            missing = sorted(set(domains) - enabled)
            if missing:
                changes.append((package, "select", missing))
        return changes

    def reconcile_links(self):
        for package, action, domains in self.link_changes():
            if action == "allow":
                self.shell("pm", "set-app-links-allowed", "--user", "0", "--package", package, "true")
            else:
                self.shell("pm", "set-app-links-user-selection", "--user", "0", "--package", package, "true", *domains)
        if self.link_changes():
            raise RuntimeError("Supported app link defaults still differ")
        print("app links: in sync")

    def privacy_changes(self):
        policy = self.profile["privacy"]
        third_party = self.shell("pm", "list", "packages", "--user", "0", "-3")
        targets = {line.removeprefix("package:") for line in third_party.splitlines() if line.startswith("package:")}
        launchers = self.shell("cmd", "package", "query-activities", "--brief", "--user", "0",
                               "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER")
        targets.update(re.findall(r"(?m)^\s*([A-Za-z][A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+$", launchers))
        targets.difference_update(policy.get("exempt", []))
        changes, protected, snapshot = [], [], {}
        for package in sorted(targets):
            permissions = owner_permissions(self.shell("dumpsys", "package", package))
            snapshot[package] = permissions
            allowed = set(policy["exceptions"].get(package, [])) | set(policy.get("allow", []))
            for permission, (granted, flags) in permissions.items():
                desired = package in policy["notifications"] if permission == "android.permission.POST_NOTIFICATIONS" else permission in allowed
                if (permission == "android.permission.ACCESS_LOCAL_NETWORK" and "REVOKE_WHEN_REQUESTED" in flags
                        and "SYSTEM_FIXED" not in flags and "POLICY_FIXED" not in flags):
                    # Android 17 keeps this compatibility grant; enforce access with its app operation.
                    op = "ACCESS_LOCAL_NETWORK"
                    current = self.shell("cmd", "appops", "get", "--user", "0", package, op)
                    snapshot.setdefault("_appops", {})[package] = current
                    denied = bool(re.search(r"ACCESS_LOCAL_NETWORK: ignore\b", current))
                    if granted and not desired and not denied:
                        changes.append(("appop", package, op, "ignore"))
                    elif desired and denied:
                        changes.append(("appop", package, op, "default"))
                    continue
                # Selected-media access remains tied to the user's photo-picker choices.
                if (not desired and not granted and "USER_FIXED" not in flags
                        and "SYSTEM_FIXED" not in flags and "POLICY_FIXED" not in flags):
                    changes.append(("flags", package, permission, "deny"))
                    continue
                if granted == desired or (desired and permission in policy.get("allow", [])):
                    continue
                if "SYSTEM_FIXED" in flags or "POLICY_FIXED" in flags:
                    protected.append(f"{package}: {permission}")
                else:
                    changes.append(("permission", package, permission, "grant" if desired else "revoke"))
        for setting, value in policy.get("secure", {}).items():
            if self.shell("settings", "--user", "0", "get", "secure", setting) != value:
                changes.append(("setting", setting, value))
        return changes, protected, snapshot

    def reconcile_privacy(self):
        changes, protected, snapshot = self.privacy_changes()
        if changes:
            backup = ROOT / ".local/backups/privacy.json"
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(json.dumps(snapshot, indent=2) + "\n")
                backup.chmod(0o600)
            appops_backup = ROOT / ".local/backups/appops.json"
            if any(change[0] == "appop" for change in changes) and not appops_backup.exists():
                appops_backup.write_text(json.dumps(snapshot.get("_appops", {}), indent=2) + "\n")
                appops_backup.chmod(0o600)
            for change in changes:
                if change[0] == "permission":
                    _, package, permission, action = change
                    if action == "grant":
                        self.shell("pm", "clear-permission-flags", "--user", "0", package, permission, "user-fixed")
                    self.shell("pm", action, "--user", "0", package, permission)
                    if action == "revoke":
                        self.shell("pm", "set-permission-flags", "--user", "0", package, permission, "user-set", "user-fixed")
                elif change[0] == "flags":
                    _, package, permission, _ = change
                    self.shell("pm", "set-permission-flags", "--user", "0", package, permission, "user-set", "user-fixed")
                elif change[0] == "appop":
                    _, package, op, mode = change
                    self.shell("cmd", "appops", "set", "--user", "0", "--uid", package, op, mode)
                else:
                    _, setting, value = change
                    self.shell("settings", "--user", "0", "put", "secure", setting, value)
            remaining, protected, _ = self.privacy_changes()
            if remaining:
                raise RuntimeError(f"Privacy policy still has {len(remaining)} changes")
        print(f"privacy: in sync ({len(protected)} permission grants managed by Android)")

    def coffee_running(self):
        # shortcut: default wake-lock mode only; add alternate-mode detection if Dragoon needs it.
        # Match a held screen lock, not Coffee's historical entries in the power log.
        return bool(re.search(r"(?m)^\s*SCREEN_(?:BRIGHT|DIM)_WAKE_LOCK\s+'Coffee::ForegroundService'.*\bACQ=",
                              self.shell("dumpsys", "power")))

    def enable_coffee(self, package):
        self.shell("input", "keyevent", "KEYCODE_WAKEUP")
        tile = package + "/.tiles.ToggleTile"
        self.shell("cmd", "statusbar", "add-tile", tile)
        # The service is private; its tile starts it through Coffee's own PendingIntent.
        self.shell("cmd", "statusbar", "click-tile", tile)
        for _ in range(20):
            if self.coffee_running():
                return
            time.sleep(0.25)
        raise RuntimeError("Coffee did not acquire its screen wake lock; unlock the phone and use Coffee's default mode")

    def tiles_drift(self):
        current = self.shell("settings", "--user", "0", "get", "secure", "sysui_qs_tiles")
        return current != ",".join(self.profile["tiles"])

    def reconcile_tiles(self):
        if self.tiles_drift():
            self.shell("cmd", "statusbar", "set-tiles", ",".join(self.profile["tiles"]))
            for _ in range(20):
                if not self.tiles_drift():
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Quick Settings tile order still differs from devices.json")
        # shortcut: non-root ADB cannot read sizes; reapply until Android exposes a getter.
        result = self.shell("cmd", "statusbar", "set-large-tiles")
        if result != "Setting large specs: []":
            raise RuntimeError(f"Could not set single-unit Quick Settings tiles: {result}")
        print("quick settings: reconciled (all tiles single-unit)")

    def drift(self, app):
        package = app["package"]
        changes = []
        code = self.version(package)
        if code != app["code"]:
            changes.append(f"version {code or 'absent'} -> {app['code']} ({app['version']})")
        if "build" in app or app.get("native"):
            if not app.get("sha256"):
                changes.append("build required")
            elif code is not None and self.installed_checksum(package) != app["sha256"]:
                changes.append("patched APK differs")
        if code is None:
            return changes
        disabled = self.shell("pm", "list", "packages", "--user", "0", "-d", package).splitlines()
        if f"package:{package}" in disabled:
            changes.append("enable app")
        if app["install"] and self.shell("cmd", "appops", "get", "--user", "0", package,
                                         "REQUEST_INSTALL_PACKAGES").split(";", 1)[0] != "REQUEST_INSTALL_PACKAGES: allow":
            changes.append("allow installing patched APKs")
        if self.wants_notifications(app):
            details = self.shell("dumpsys", "package", package)
            owner = re.search(r"(?ms)^\s*User 0:.*?(?=^\s*User \d+:|\Z)", details)
            if not owner:
                raise RuntimeError(f"Cannot read owner permissions for {package}")
            if "android.permission.POST_NOTIFICATIONS: granted=true" not in owner[0]:
                changes.append("allow notifications")
        if app["background"]:
            whitelist = self.shell("dumpsys", "deviceidle", "whitelist").splitlines()
            if not any(line.split(",")[1:2] == [package] for line in whitelist):
                changes.append("allow background operation")
        if app.get("keep_awake") and not self.coffee_running():
            changes.append("activate Coffee")
        return changes

    def reconcile(self, name, app, yolo):
        package = app["package"]
        installed = self.version(package)
        if installed is not None and installed > app["code"]:
            raise RuntimeError(f"{name} is newer than the pin; update apps.json. Downgrades are refused.")
        differs = installed is not None and ("build" in app or app.get("native")) and self.installed_checksum(package) != app["sha256"]
        if installed != app["code"] or differs:
            apk = Path(app["path"]) if "path" in app else download(name, app)
            if "path" in app and checksum(apk) != app["sha256"]:
                raise RuntimeError(f"Checksum mismatch for {name}; refusing installation")
            print(f"Install {name} {app['version']}", flush=True)
            try:
                result = self.install_apk(apk, app)
                if "Success" not in result:
                    raise RuntimeError(result)
            except RuntimeError as error:
                if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" not in str(error):
                    raise
                warning = (f"{name} has a different signing key. Uninstalling {package} erases its data in every Android profile. "
                           "For Morphe this also erases the patch signing keystore; export it first. Continue?")
                if not confirm(warning, yolo):
                    raise RuntimeError(f"Preserved {name}: signature conflict requires an explicit destructive reinstall") from error
                result = self.adb("uninstall", package)
                if result != "Success":
                    raise RuntimeError(result)
                result = self.install_apk(apk, app, replace=False)
                if "Success" not in result:
                    raise RuntimeError(result)
            if self.version(package) != app["code"]:
                raise RuntimeError(f"Installed {name} does not match its pinned version")
        changes = self.drift(app)
        if "enable app" in changes:
            self.shell("pm", "enable", "--user", "0", package)
        if "allow installing patched APKs" in changes:
            self.shell("cmd", "appops", "set", "--user", "0", package, "REQUEST_INSTALL_PACKAGES", "allow")
        if "allow notifications" in changes:
            self.shell("pm", "grant", "--user", "0", package, "android.permission.POST_NOTIFICATIONS")
        if "allow background operation" in changes:
            self.shell("dumpsys", "deviceidle", "whitelist", f"+{package}")
        if "activate Coffee" in changes:
            self.enable_coffee(package)
        remaining = self.drift(app)
        if remaining:
            raise RuntimeError(f"{name} still differs: {', '.join(remaining)}")
        print(f"{name}: reconciled ({app['version']})")

    def save_transport(self, transport):
        bindings = read("devices.local.json")
        bindings[self.name]["transport"] = transport
        path = ROOT / "devices.local.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(bindings, indent=2) + "\n")
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "check", "install", "open", "home", "privacy", "pixel", "pair", "connect", "usb"])
    parser.add_argument("device")
    parser.add_argument("target", nargs="?")
    parser.add_argument("--yolo", choices=["true", "false"], default="false")
    args = parser.parse_args()
    device = Device(args.device)
    if args.action in {"pair", "connect"}:
        if not args.target or not re.fullmatch(r"[A-Za-z0-9.\[\]:%-]+:\d{1,5}", args.target):
            raise ValueError("Use the address:port shown in Android Wireless debugging")
        if args.action == "pair":
            subprocess.run(["adb", "pair", args.target], check=True)
            return
        print(run("adb", "connect", args.target))
        device.transport = args.target
        device.verify()
        device.save_transport(args.target)
        return
    if args.action == "usb":
        device.transport = device.serial
        device.verify()
        device.save_transport(device.serial)
        return
    apps = read("apps.json")
    names = device.profile["apps"]
    if device.profile.get("home") and args.action in {"setup", "home", "check"}:
        layout = device.profile["home"]
        required = list(dict.fromkeys(name for row in layout["rows"] + [layout["dock"]] for name in row if name in apps))
        if {"youtube", "music"}.intersection(required) and "microg" in apps:
            required.insert(0, "microg")
        names = required if args.action == "home" else list(dict.fromkeys(names + required))
    if args.action in {"install", "open"}:
        if args.target not in names:
            raise ValueError(f"Choose an app declared for {args.device}: {', '.join(names)}")
        names = [args.target]
    device.verify()
    if args.action == "pixel":
        print(device.shell("cmd", "package", "set-home-activity", "--user", "0", "com.google.android.apps.nexuslauncher/.NexusLauncherActivity"))
        return 0
    if args.action == "privacy":
        device.reconcile_privacy()
        if "coffee" in device.profile["apps"]:
            device.reconcile("coffee", apps["coffee"], args.yolo == "true")
        return 0
    if args.action != "check" and "coffee" in device.profile["apps"]:
        device.reconcile("coffee", apps["coffee"], args.yolo == "true")
        names = [name for name in names if name != "coffee"]
    dirty = False
    if device.profile.get("tiles"):
        if args.action == "check":
            dirty = device.tiles_drift()
            print(f"quick settings: {'tile order differs' if dirty else 'tile order in sync'} (sizes reapplied on modifying runs)")
        else:
            device.reconcile_tiles()
    if args.action == "open":
        package = apps[args.target]["package"]
        activity = apps[args.target].get("activity")
        if not activity:
            resolved = device.shell("cmd", "package", "resolve-activity", "--brief", "--user", "0",
                                    "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package)
            activity = resolved.splitlines()[-1] if resolved else ""
        if not activity.startswith(package + "/"):
            raise ValueError(f"No launcher activity found for {package}")
        print(device.shell("am", "start", "-W", "-n", activity))
        return
    for name in names:
        app = artifact(name, apps[name], create=args.action != "check", device=device)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]+", app["package"]):
            raise ValueError("Invalid Android package name")
        if args.action == "check":
            changes = device.drift(app)
            print(f"{name}: {', '.join(changes) if changes else 'in sync'}")
            dirty |= bool(changes)
        else:
            device.reconcile(name, app, args.yolo == "true")
    if device.profile.get("home") and args.action in {"setup", "home", "check"}:
        if args.action == "check":
            changes = device.home_drift()
            print(f"home: {', '.join(changes) if changes else 'in sync'}")
            dirty |= bool(changes)
        else:
            device.reconcile_home(args.yolo == "true")
    if device.profile.get("remove_stock") and args.action in {"setup", "home", "check"}:
        if args.action == "check":
            duplicates = device.stock_duplicates()
            print(f"stock duplicates: {', '.join(duplicates) if duplicates else 'none'}")
            dirty |= bool(duplicates)
        else:
            device.reconcile_stock_duplicates()
    if device.profile.get("links") and args.action in {"setup", "check"}:
        if args.action == "check":
            changes = device.link_changes()
            print(f"app links: {len(changes)} changes")
            dirty |= bool(changes)
        else:
            device.reconcile_links()
    if device.profile.get("privacy"):
        if args.action == "check":
            changes, protected, _ = device.privacy_changes()
            print(f"privacy: {len(changes)} changes, {len(protected)} Android-managed grants")
            dirty |= bool(changes)
        else:
            device.reconcile_privacy()
            if "coffee" in device.profile["apps"]:
                device.reconcile("coffee", apps["coffee"], args.yolo == "true")
    if dirty:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
