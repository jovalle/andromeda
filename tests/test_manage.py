import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, call, patch

import manage


APP = manage.read("apps.json")["morphe"]


def bound_device():
    profiles = {"dragoon": {"model": "Pixel 10 Pro", "abi": "arm64-v8a", "apps": ["morphe"]}}
    bindings = {"dragoon": {"serial": "test-serial"}}
    with patch("pathlib.Path.exists", return_value=True), patch("manage.read", side_effect=[profiles, bindings]):
        return manage.Device("dragoon")


class FakeDevice(manage.Device):
    """Model package state and reject any unexpected command."""

    def __init__(self, code=None, app=APP):
        self.app = app
        self.profile = {}
        self.awake = False
        self.wake_failure = False
        self.code = code
        self.calls = []
        self.allowed = False
        self.notified = False
        self.background = False
        self.disabled = False
        self.conflict = False

    def adb(self, *args):
        self.calls.append(args)
        if args[0] == "install":
            if self.conflict:
                raise RuntimeError("INSTALL_FAILED_UPDATE_INCOMPATIBLE")
            self.code = self.app["code"]
            return "Success"
        if args[0] == "uninstall":
            self.code = None
            self.conflict = False
            return "Success"
        raise AssertionError(args)

    def shell(self, *args):
        self.calls.append(args)
        if args[:3] == ("pm", "list", "packages"):
            if "-d" in args and not self.disabled:
                return ""
            return f"package:{self.app['package']}" if self.code else ""
        if args[:2] == ("dumpsys", "package"):
            granted = str(self.notified).lower()
            return (f"versionCode={self.code} minSdk=26\nversionName=2.2.0\n"
                    f"  User 0: installed=true\n    android.permission.POST_NOTIFICATIONS: granted={granted}\n"
                    "  User 10: installed=true\n    android.permission.POST_NOTIFICATIONS: granted=true")
        if args[:3] == ("cmd", "appops", "get"):
            return "REQUEST_INSTALL_PACKAGES: " + ("allow; time=+1s ago" if self.allowed else "default")
        if args[:3] == ("cmd", "appops", "set"):
            self.allowed = True
            return ""
        if args[:2] == ("pm", "grant"):
            self.notified = True
            return ""
        if args[:2] == ("pm", "enable"):
            self.disabled = False
            return ""
        if args[:3] == ("dumpsys", "deviceidle", "whitelist"):
            if len(args) == 4:
                self.background = True
            return f"user,{self.app['package']},10234" if self.background else "system,other.app,1000"
        if args == ("dumpsys", "power"):
            return ("  SCREEN_BRIGHT_WAKE_LOCK 'Coffee::ForegroundService' ON_AFTER_RELEASE ACQ=-1s"
                    if self.awake else "  10-25 11:56 - ACQ Coffee::ForegroundService (screen-bright)")
        if args == ("input", "keyevent", "KEYCODE_WAKEUP"):
            return ""
        if args[:2] == ("cmd", "statusbar"):
            if args[2] == "click-tile" and not self.wake_failure:
                self.awake = not self.awake
            return ""
        raise AssertionError(args)


class ReconcileTests(unittest.TestCase):
    @patch("manage.download", return_value=Path("/tmp/pinned.apk"))
    def test_install_permissions_and_repeat_is_read_only(self, download):
        device = FakeDevice()
        self.assertTrue(device.drift(APP))
        device.reconcile("morphe", APP, False)
        self.assertEqual(device.drift(APP), [])
        device.calls.clear()
        device.reconcile("morphe", APP, False)
        mutations = [c for c in device.calls if c[0] in {"install", "uninstall"}
                     or c[:2] in {("pm", "grant"), ("pm", "enable")}
                     or c[:3] == ("cmd", "appops", "set")
                     or (c[:3] == ("dumpsys", "deviceidle", "whitelist") and len(c) > 3)]
        self.assertEqual(mutations, [])
        download.assert_called_once()

    @patch("manage.download", return_value=Path("/tmp/pinned.apk"))
    def test_update_preserves_data(self, download):
        device = FakeDevice(APP["code"] - 1)
        device.reconcile("morphe", APP, False)
        self.assertIn(("install", "-r", "/tmp/pinned.apk"), device.calls)
        self.assertFalse(any(c[0] == "uninstall" for c in device.calls))

    @patch("manage.download")
    def test_newer_version_is_preserved(self, download):
        device = FakeDevice(APP["code"] + 1)
        with self.assertRaisesRegex(RuntimeError, "Downgrades are refused"):
            device.reconcile("morphe", APP, True)
        download.assert_not_called()

    @patch("manage.download", return_value=Path("/tmp/pinned.apk"))
    def test_yolo_never_erases_conflicting_app(self, download):
        device = FakeDevice(APP["code"] - 1)
        device.conflict = True
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(RuntimeError, "Preserved"):
                device.reconcile("morphe", APP, True)
        prompt.assert_not_called()
        self.assertFalse(any(c[0] == "uninstall" for c in device.calls))

    @patch("manage.download", return_value=Path("/tmp/pinned.apk"))
    def test_conflict_requires_explicit_consent(self, download):
        device = FakeDevice(APP["code"] - 1)
        device.conflict = True
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "Preserved"):
                device.reconcile("morphe", APP, False)
        self.assertFalse(any(c[0] == "uninstall" for c in device.calls))
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="yes"):
            device.reconcile("morphe", APP, False)
        self.assertIn(("uninstall", APP["package"]), device.calls)

    def test_wrong_device_is_rejected_before_mutation(self):
        device = bound_device()
        with patch.object(device, "adb", return_value="device"), patch.object(device, "shell", return_value="other"):
            with self.assertRaisesRegex(RuntimeError, "Identity mismatch"):
                device.verify()

    def test_disabled_app_is_reenabled(self):
        device = FakeDevice(APP["code"])
        device.disabled = True
        device.reconcile("morphe", APP, False)
        self.assertFalse(device.disabled)

    def test_work_profile_permission_does_not_hide_owner_drift(self):
        device = FakeDevice(APP["code"])
        self.assertIn("allow notifications", device.drift(APP))

    def test_patch_change_at_same_version_is_installed(self):
        device = FakeDevice(APP["code"])
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "patched.apk"
            apk.write_bytes(b"patched app")
            digest = manage.checksum(apk)
            app = dict(APP, build={}, path=str(apk), sha256=digest)
            with patch.object(device, "installed_checksum", side_effect=["old", digest, digest]):
                device.reconcile("morphe", app, False)
        self.assertTrue(any(c[:2] == ("install", "-r") for c in device.calls))

    def test_remote_shell_arguments_are_quoted(self):
        device = bound_device()
        with patch.object(device, "adb", return_value="") as adb:
            device.shell("echo", "hello; exit 1")
        adb.assert_called_once_with("shell", "echo 'hello; exit 1'")


class OpenTests(unittest.TestCase):
    def test_explicit_launcher_avoids_resolver_and_stays_in_the_package(self):
        apps = {"messenger": {"package": "com.facebook.orca", "activity": "com.facebook.orca/.auth.StartScreenActivity"}}
        for activity, valid in [(apps["messenger"]["activity"], True), ("other.app/.Activity", False)]:
            apps["messenger"]["activity"] = activity
            device = Mock(profile={"apps": ["messenger"]})
            with patch("sys.argv", ["manage.py", "open", "dragoon", "messenger"]), \
                    patch("manage.Device", return_value=device), patch("manage.read", return_value=apps):
                if valid:
                    manage.main()
                    device.shell.assert_called_once_with("am", "start", "-W", "-n", activity)
                else:
                    with self.assertRaisesRegex(ValueError, "No launcher activity"):
                        manage.main()
                    device.shell.assert_not_called()


class CoffeeTests(unittest.TestCase):
    def test_activation_and_repeat_never_toggles_it_off(self):
        app = manage.read("apps.json")["coffee"]
        device = FakeDevice(app["code"], app)
        self.assertIn("activate Coffee", device.drift(app))
        device.reconcile("coffee", app, True)
        self.assertTrue(device.awake)
        device.reconcile("coffee", app, True)
        clicks = [c for c in device.calls if c[:3] == ("cmd", "statusbar", "click-tile")]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(device.drift(app), [])

    @patch("manage.time.sleep")
    def test_failed_activation_is_reported_without_repeated_toggles(self, sleep):
        app = manage.read("apps.json")["coffee"]
        device = FakeDevice(app["code"], app)
        device.wake_failure = True
        with self.assertRaisesRegex(RuntimeError, "did not acquire"):
            device.reconcile("coffee", app, True)
        self.assertEqual(sum(c[:3] == ("cmd", "statusbar", "click-tile") for c in device.calls), 1)

    def test_coffee_precedes_commands_and_check_is_read_only(self):
        apps = manage.read("apps.json")
        for action, target in [("setup", None), ("install", "microg"), ("open", "microg"),
                               ("install", "coffee"), ("check", None)]:
            with self.subTest(action=action, target=target):
                device = Mock(profile={"apps": ["microg", "coffee"], "tiles": ["wifi"]})
                device.tiles_drift.return_value = False
                device.drift.return_value = []
                device.shell.return_value = apps["microg"]["package"] + "/.MainActivity"
                argv = ["manage.py", action, "dragoon"] + ([target] if target else [])
                with patch("sys.argv", argv), patch("manage.Device", return_value=device), \
                        patch("manage.artifact", side_effect=lambda name, app, **kwargs: app):
                    manage.main()
                self.assertEqual(device.mock_calls[0], call.verify())
                if action == "check":
                    device.reconcile.assert_not_called()
                    device.reconcile_tiles.assert_not_called()
                    device.tiles_drift.assert_called_once_with()
                    device.shell.assert_not_called()
                else:
                    self.assertEqual(device.mock_calls[1], call.reconcile("coffee", apps["coffee"], False))
                    self.assertEqual(device.mock_calls[2], call.reconcile_tiles())
                    self.assertEqual(sum(c.args[0] == "coffee" for c in device.reconcile.call_args_list), 1)

    def test_identity_failure_prevents_coffee_preflight(self):
        device = Mock(profile={"apps": ["coffee"]})
        device.verify.side_effect = RuntimeError("Identity mismatch")
        with patch("sys.argv", ["manage.py", "setup", "dragoon"]), patch("manage.Device", return_value=device):
            with self.assertRaisesRegex(RuntimeError, "Identity mismatch"):
                manage.main()
        device.reconcile.assert_not_called()


class TileTests(unittest.TestCase):
    def test_exact_order_removes_extras_and_repeated_runs_keep_small_tiles(self):
        device = bound_device()
        device.profile["tiles"] = manage.read("devices.json")["dragoon"]["tiles"]
        desired = ",".join(device.profile["tiles"])
        current = "flashlight,wifi,bt"

        def shell(*args):
            nonlocal current
            if args == ("settings", "--user", "0", "get", "secure", "sysui_qs_tiles"):
                return current
            if args == ("cmd", "statusbar", "set-tiles", desired):
                current = desired
                return ""
            if args == ("cmd", "statusbar", "set-large-tiles"):
                return "Setting large specs: []"
            raise AssertionError(args)

        with patch.object(device, "shell", side_effect=shell) as execute:
            self.assertTrue(device.tiles_drift())
            execute.assert_called_once_with("settings", "--user", "0", "get", "secure", "sysui_qs_tiles")
            device.reconcile_tiles()
            self.assertEqual(current, desired)
            device.reconcile_tiles()
            self.assertEqual(execute.call_args_list.count(call("cmd", "statusbar", "set-tiles", desired)), 1)
            self.assertEqual(execute.call_args_list.count(call("cmd", "statusbar", "set-large-tiles")), 2)

    @patch("manage.time.sleep")
    def test_failed_order_write_is_reported(self, sleep):
        device = bound_device()
        device.profile["tiles"] = ["wifi"]
        with patch.object(device, "shell", return_value="unchanged") as execute:
            with self.assertRaisesRegex(RuntimeError, "tile order still differs"):
                device.reconcile_tiles()
        self.assertNotIn(call("cmd", "statusbar", "set-large-tiles"), execute.call_args_list)

    def test_unsupported_sizing_command_is_reported(self):
        device = bound_device()
        device.profile["tiles"] = ["wifi"]
        with patch.object(device, "shell", side_effect=["wifi", "Unknown command"]):
            with self.assertRaisesRegex(RuntimeError, "Could not set single-unit"):
                device.reconcile_tiles()

    def test_check_returns_failure_for_tile_drift_without_mutation(self):
        device = Mock(profile={"apps": [], "tiles": ["wifi"]})
        device.tiles_drift.return_value = True
        with patch("sys.argv", ["manage.py", "check", "dragoon"]), patch("manage.Device", return_value=device):
            self.assertEqual(manage.main(), 1)
        self.assertEqual(device.mock_calls, [call.verify(), call.tiles_drift()])


class DownloadTests(unittest.TestCase):
    def test_hash_verified_cache_and_corruption(self):
        data = b"official APK fixture"
        app = dict(APP, sha256=hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)):
            with patch("urllib.request.urlopen", return_value=io.BytesIO(data)) as fetch:
                path = manage.download("morphe", app)
                self.assertEqual(path.read_bytes(), data)
            with patch("urllib.request.urlopen") as fetch:
                self.assertEqual(manage.download("morphe", app), path)
                fetch.assert_not_called()
            path.write_bytes(b"corrupt")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                    manage.download("morphe", app)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob("*.part")), [])

    def test_untrusted_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned HTTPS GitHub release or F-Droid APK"):
            manage.download("morphe", dict(APP, url="https://example.com/morphe.apk"))

    def test_fdroid_download_is_pinned_and_hash_checked(self):
        app = manage.read("apps.json")["coffee"]
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)), \
                patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong APK")):
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                manage.download("coffee", app)
            self.assertEqual(list((Path(temporary) / ".cache/apks").iterdir()), [])
        for url in ["http://f-droid.org/repo/coffee_47.apk", "https://f-droid.org/repo/coffee.apk",
                    "https://f-droid.org.evil/repo/coffee_47.apk", "https://f-droid.org/repo/coffee_47.apk?latest=1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                manage.download("coffee", dict(app, url=url))


class BuildTests(unittest.TestCase):
    def test_facebook_profile_patch_source_invalidates_cached_build(self):
        app, tools = manage.read("apps.json")["facebook"], manage.read("builds.json")
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)), patch("manage.read", return_value=tools):
            root = Path(temporary)
            key = root / ".local/morphe.keystore"
            key.parent.mkdir()
            key.write_bytes(b"retained key")
            source = root / "patches/FacebookProfileLinks.java"
            source.parent.mkdir()
            source.write_bytes(b"profile patch v1")
            base_app = dict(app, build={name: value for name, value in app["build"].items() if name != "profile_links"})
            base_spec = {"app": base_app, "tools": {name: tools[name] for name in ("desktop", "facebook")},
                         "key": manage.checksum(key), "mode": "FULL", "abi": "arm64-v8a", "alias": "morphe"}
            base_folder = root / ".cache/builds" / hashlib.sha256(json.dumps(base_spec, sort_keys=True).encode()).hexdigest()
            base_folder.mkdir(parents=True)
            base_apk = base_folder / "app.apk"
            base_apk.write_bytes(b"baseline Facebook")
            (base_folder / "artifact.json").write_text(json.dumps({"sha256": manage.checksum(base_apk)}))
            build_app = dict(app, profile_links_source=manage.checksum(source), profile_links_base=manage.checksum(base_apk))
            spec = {"app": build_app, "tools": {name: tools[name] for name in ("desktop", "facebook")},
                    "key": manage.checksum(key), "mode": "FULL", "abi": "arm64-v8a", "alias": "morphe"}
            folder = root / ".cache/builds" / hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
            folder.mkdir(parents=True)
            apk = folder / "app.apk"
            apk.write_bytes(b"patched Facebook")
            (folder / "artifact.json").write_text(json.dumps({"sha256": manage.checksum(apk)}))
            self.assertEqual(manage.artifact("facebook", app)["path"], str(apk))
            source.write_bytes(b"profile patch v2")
            self.assertIsNone(manage.artifact("facebook", app)["sha256"])
            self.assertEqual(key.read_bytes(), b"retained key")

    def test_facebook_postprocessing_preserves_payload_and_resigns_after_alignment(self):
        with tempfile.TemporaryDirectory() as temporary:
            apk, key = Path(temporary) / "facebook.apk", Path(temporary) / "key"
            key.write_bytes(b"retained key")
            payload = {"AndroidManifest.xml": b"old manifest", "classes.dex": b"untouched code",
                       "lib/arm64-v8a/example.so": b"untouched native library", "assets/config": b"untouched asset",
                       "META-INF/services/provider": b"retained service metadata", "META-INF/MANIFEST.MF": b"old signature",
                       "META-INF/MORPHE.SF": b"old signature", "META-INF/MORPHE.RSA": b"old certificate"}
            with zipfile.ZipFile(apk, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in payload.items(): archive.writestr(name, data)
            operations = []
            def execute(*args):
                if "--class-path" in args:
                    Path(args[-1]).write_bytes(b"new manifest")
                elif args[:2] == ("zipalign", "-P"):
                    operations.append("align")
                    shutil.copyfile(args[-2], args[-1])
                elif "apksigner" in args and "sign" in args:
                    operations.append("sign")
                    shutil.copyfile(args[-1], args[args.index("--out") + 1])
                elif args[:2] == ("zipalign", "-c"):
                    operations.append("check alignment")
                else:
                    raise AssertionError(args)
                return ""
            with patch("manage.run", side_effect=execute), patch("manage.java", return_value=Path("/jdk/bin/java")), \
                    patch("manage.sdktool", side_effect=lambda name: name):
                manage.facebook_profile_links(apk, Path("pinned-cli.jar"), key)
            self.assertEqual(operations, ["align", "sign", "check alignment"])
            with zipfile.ZipFile(apk) as archive:
                self.assertEqual(archive.read("AndroidManifest.xml"), b"new manifest")
                for name in ["classes.dex", "lib/arm64-v8a/example.so", "assets/config", "META-INF/services/provider"]:
                    self.assertEqual(archive.read(name), payload[name])
                self.assertNotIn("META-INF/MORPHE.RSA", archive.namelist())
                self.assertNotIn("META-INF/MORPHE.SF", archive.namelist())
                self.assertNotIn("META-INF/MANIFEST.MF", archive.namelist())
            self.assertEqual(key.read_bytes(), b"retained key")

    def test_private_patch_options_require_input_and_change_build_inputs(self):
        patches = [{"name": "Spoof client", "options": {"redirect-uri": "redreader://rr_oauth_redir"},
                    "options_file": ".local/rif.json"}]
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)):
            root = Path(temporary)
            self.assertIsNone(manage.patch_options(patches))
            with self.assertRaisesRegex(RuntimeError, "Provide private patch options"):
                manage.patch_options(patches, required=True)
            self.assertEqual(list(root.iterdir()), [])
            path = root / ".local/rif.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"client-id": "test-client-one"}))
            first = manage.patch_options(patches)
            self.assertEqual(first[0]["options"], {"redirect-uri": "redreader://rr_oauth_redir", "client-id": "test-client-one"})
            self.assertNotIn("client-id", patches[0]["options"])
            path.write_text(json.dumps({"client-id": "test-client-two"}))
            self.assertNotEqual(first, manage.patch_options(patches))
            for invalid in [[], {}, {"client-id": ""}, {"client-id": " padded"}, {"client-id": 123}]:
                path.write_text(json.dumps(invalid))
                with self.assertRaisesRegex(ValueError, "nonempty string patch options"):
                    manage.patch_options(patches)

    def test_identity_reads_manifest_without_resolving_android_icons(self):
        manifest = '''N: android=http://schemas.android.com/apk/res/android
  E: manifest (line=1)
    A: android:versionCode(0x0101021b)=(type 0x10)0x14abf9ae
    A: android:versionName(0x0101021c)="584.0.0.49.98" (Raw: "584.0.0.49.98")
    A: package="com.facebook.orca" (Raw: "com.facebook.orca")
    E: application (line=2)
      A: android:icon(0x01010002)=@0x01080049
'''
        with patch("manage.sdktool", return_value="aapt"), patch("manage.run", return_value=manifest) as command:
            self.assertEqual(manage.apk_identity("messenger.apk"), ("com.facebook.orca", 346814894, "584.0.0.49.98"))
            command.assert_called_once_with("aapt", "dump", "xmltree", "messenger.apk", "AndroidManifest.xml")
        with patch("manage.sdktool", return_value="aapt"), patch("manage.run", return_value='E: manifest\n  E: application\n    A: package="wrong"'):
            with self.assertRaisesRegex(RuntimeError, "Cannot read APK package/version"):
                manage.apk_identity("bad.apk")

    def test_check_does_not_create_a_key_or_download(self):
        app = manage.read("apps.json")["youtube"]
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)), \
                patch("manage.read", return_value={}), patch("manage.download") as download:
            self.assertIsNone(manage.artifact("youtube", app)["sha256"])
            self.assertEqual(list(Path(temporary).iterdir()), [])
            download.assert_not_called()

    def test_build_inputs_and_signing_key_invalidate_cache(self):
        app = manage.read("apps.json")["youtube"]
        tools = manage.read("builds.json")
        with tempfile.TemporaryDirectory() as temporary, patch("manage.ROOT", Path(temporary)), \
                patch("manage.read", return_value=tools):
            root = Path(temporary)
            key = root / ".local/morphe.keystore"
            key.parent.mkdir()
            key.write_bytes(b"signing key fixture")
            selected_tools = {name: tools[name] for name in ("desktop", app["build"]["patches"])}
            spec = {"app": app, "tools": selected_tools, "key": manage.checksum(key),
                    "mode": "FULL", "abi": "arm64-v8a", "alias": "morphe"}
            fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
            folder = root / ".cache/builds" / fingerprint
            folder.mkdir(parents=True)
            apk = folder / "app.apk"
            apk.write_bytes(b"signed fixture")
            (folder / "artifact.json").write_text(json.dumps({"sha256": manage.checksum(apk)}))
            self.assertEqual(manage.artifact("youtube", app)["path"], str(apk))
            launcher = dict(app, activity="app.morphe.android.youtube/.MainActivity")
            self.assertEqual(manage.artifact("youtube", launcher)["path"], str(apk))
            unrelated = dict(tools, unrelated={"version": "new"})
            with patch("manage.read", return_value=unrelated):
                self.assertEqual(manage.artifact("youtube", app)["path"], str(apk))
            changed = dict(app, build=dict(app["build"], enable=[{"name": "Something"}]))
            self.assertIsNone(manage.artifact("youtube", changed)["sha256"])
            key.write_bytes(b"different signing key")
            self.assertIsNone(manage.artifact("youtube", app)["sha256"])
            key.unlink()
            with self.assertRaisesRegex(RuntimeError, "Signing key is missing"), patch("manage.run") as execute:
                manage.artifact("youtube", app, create=True)
            execute.assert_not_called()

    def test_capture_is_deterministic_and_does_not_accept_changed_originals(self):
        app = {"code": 2147483647, "build": {"device": "com.instagram.android", "code": 123}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = root / "expected.apkm"
            with zipfile.ZipFile(expected, "w", zipfile.ZIP_STORED) as archive:
                archive.writestr("base.apk", b"base")
                archive.writestr("split.apk", b"split")
            app["build"]["sha256"] = manage.checksum(expected)
            device = unittest.mock.Mock(name="dragoon")
            device.version.return_value = 123
            device.shell.return_value = "package:/data/app/base.apk\npackage:/data/app/split.apk"
            def pull(action, remote, local):
                self.assertEqual(action, "pull")
                Path(local).write_bytes(b"base" if remote.endswith("base.apk") else b"split")
            device.adb.side_effect = pull
            target = root / "inputs/instagram.apkm"
            manage.capture(device, app, target)
            self.assertEqual(manage.checksum(target), app["build"]["sha256"])
            before = target.read_bytes()
            app["build"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "differs"):
                manage.capture(device, app, target)
            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
