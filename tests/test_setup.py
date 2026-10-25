import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import manage
from test_manage import bound_device


def package_permissions(values, extra=""):
    lines = ["    User 0: installed=true", "      runtime permissions:"]
    for permission, (granted, flags) in values.items():
        lines.append(f"        android.permission.{permission}: granted={str(granted).lower()}, flags=[{flags}]")
    return "\n".join(lines) + "\n" + extra


class SetupTests(unittest.TestCase):
    def test_owner_app_links_ignores_verification_and_other_profiles(self):
        details = ("package:\n  Domain verification state:\n    other.com: verified\n"
                   "  User 0:\n    Verification link handling allowed: false\n    Selection state:\n"
                   "      Enabled:\n        reddit.com\n      Disabled:\n        *.redd.it\n"
                   "  User 10:\n    Verification link handling allowed: true\n    Selection state:\n"
                   "      Enabled:\n        other.com\n")
        self.assertEqual(manage.owner_app_links(details), (False, {"reddit.com"}, {"reddit.com", "*.redd.it"}))
        with self.assertRaisesRegex(RuntimeError, "owner-profile app links"):
            manage.owner_app_links("User 10:\n")
        with self.assertRaisesRegex(RuntimeError, "selection state"):
            manage.owner_app_links("User 0:\n  unexpected output\n")

    def test_link_defaults_enable_supported_hosts_and_repeat_without_writes(self):
        device = bound_device()
        package = manage.read("apps.json")["reddit"]["package"]
        device.profile["links"] = {"reddit": ["reddit.com", "*.redd.it"]}
        allowed, enabled, writes = False, set(), []
        def shell(*args):
            nonlocal allowed
            if args == ("pm", "get-app-links", "--user", "0", package):
                lines = ["User 0:", f"  Verification link handling allowed: {str(allowed).lower()}",
                         "  Selection state:", "    Enabled:"]
                lines += ["      " + host for host in sorted(enabled)]
                lines += ["    Disabled:"] + ["      " + host for host in sorted({"reddit.com", "*.redd.it"} - enabled)]
                return "\n".join(lines)
            writes.append(args)
            if args[:2] == ("pm", "set-app-links-allowed"):
                allowed = True
            elif args[:2] == ("pm", "set-app-links-user-selection"):
                enabled.update(args[7:])
            else:
                raise AssertionError(args)
            return ""
        with patch.object(device, "shell", side_effect=shell):
            self.assertEqual(len(device.link_changes()), 2)
            self.assertEqual(writes, [])
            device.reconcile_links()
            device.reconcile_links()
            device.profile["links"]["reddit"].append("unrelated.example")
            with self.assertRaisesRegex(ValueError, "does not declare"):
                device.reconcile_links()
        self.assertEqual(writes, [
            ("pm", "set-app-links-allowed", "--user", "0", "--package", package, "true"),
            ("pm", "set-app-links-user-selection", "--user", "0", "--package", package, "true", "*.redd.it", "reddit.com")])

    def test_conflicting_link_owners_are_rejected_before_any_write(self):
        device = bound_device()
        device.profile["links"] = {"reddit": ["reddit.com"], "rif": ["reddit.com"]}
        state = "User 0:\n  Verification link handling allowed: true\n  Selection state:\n    Disabled:\n      reddit.com\n"
        with patch.object(device, "shell", return_value=state) as shell:
            with self.assertRaisesRegex(ValueError, "Conflicting link owners"):
                device.reconcile_links()
        self.assertTrue(all(c.args[:2] == ("pm", "get-app-links") for c in shell.call_args_list))

    def test_failed_link_selection_is_reported(self):
        device = bound_device()
        device.profile["links"] = {"reddit": ["reddit.com"]}
        state = "User 0:\n  Verification link handling allowed: true\n  Selection state:\n    Disabled:\n      reddit.com\n"
        with patch.object(device, "shell", return_value=state):
            with self.assertRaisesRegex(RuntimeError, "defaults still differ"):
                device.reconcile_links()

    def test_check_reports_link_drift_without_mutation(self):
        device = Mock(profile={"apps": [], "links": {"reddit": ["reddit.com"]}})
        device.link_changes.return_value = [("app.morphe.reddit", "select", ["reddit.com"])]
        with patch("sys.argv", ["manage.py", "check", "dragoon"]), patch("manage.Device", return_value=device):
            self.assertEqual(manage.main(), 1)
        self.assertEqual([c[0] for c in device.mock_calls], ["verify", "link_changes"])

    def test_owner_permissions_do_not_include_other_profiles_or_hidden_system_package(self):
        text = package_permissions({"CAMERA": (False, "USER_SET")},
            "    User 10: installed=true\n      runtime permissions:\n"
            "        android.permission.CAMERA: granted=true, flags=[]\n"
            "Hidden system packages:\n    User 0: installed=true\n      runtime permissions:\n"
            "        android.permission.RECORD_AUDIO: granted=true, flags=[]")
        self.assertEqual(manage.owner_permissions(text), {"android.permission.CAMERA": (False, "USER_SET")})
        with self.assertRaisesRegex(RuntimeError, "owner-profile"):
            manage.owner_permissions("User 10: installed=true")

    def test_shared_uid_owner_permissions_and_core_exception_grants(self):
        details = ("Packages:\n  Package [core.maps]:\n    sharedUser=SharedUserSetting{abc core.maps/12345}\n"
                   "    User 0: installed=true\nHidden system packages:\n    User 0: installed=true\n"
                   "Shared users:\n  SharedUser [core.maps] (abc):\n" +
                   package_permissions({"ACCESS_FINE_LOCATION": (False, "USER_SET")}) + "\nDexopt state:\n")
        self.assertEqual(manage.owner_permissions(details), {"android.permission.ACCESS_FINE_LOCATION": (False, "USER_SET")})
        device = bound_device()
        device.profile["privacy"] = {"notifications": [], "exceptions": {"core.maps": ["android.permission.ACCESS_FINE_LOCATION"]}}
        def shell(*args):
            if args[:2] == ("pm", "list"): return "package:core.maps"
            if args[:3] == ("cmd", "package", "query-activities"): return ""
            if args[:2] == ("dumpsys", "package"): return details
            raise AssertionError(args)
        with patch.object(device, "shell", side_effect=shell):
            self.assertEqual(device.privacy_changes()[0], [("permission", "core.maps", "android.permission.ACCESS_FINE_LOCATION", "grant")])

    def test_strict_policy_preserves_core_exceptions_and_android_fixed_permissions(self):
        device = bound_device()
        device.profile["privacy"] = {
            "notifications": ["core.phone"], "exceptions": {"core.phone": ["android.permission.RECORD_AUDIO"]},
            "exempt": ["android.system"], "secure": {"lock_screen_allow_private_notifications": "0"}}
        permissions = {"POST_NOTIFICATIONS": (True, ""), "CAMERA": (True, ""),
                       "RECORD_AUDIO": (True, ""), "READ_CONTACTS": (True, "SYSTEM_FIXED")}
        def shell(*args):
            if args[:2] == ("pm", "list"):
                return "package:social.app\npackage:android.system"
            if args[:3] == ("cmd", "package", "query-activities"):
                return "  core.phone/.Main\n  android.system/.Main"
            if args[:2] == ("dumpsys", "package"):
                self.assertNotEqual(args[2], "android.system")
                return package_permissions(permissions)
            if args[0] == "settings":
                return "0"
            raise AssertionError(args)
        with patch.object(device, "shell", side_effect=shell):
            changes, protected, _ = device.privacy_changes()
        self.assertIn(("permission", "social.app", "android.permission.POST_NOTIFICATIONS", "revoke"), changes)
        self.assertNotIn(("permission", "core.phone", "android.permission.RECORD_AUDIO", "revoke"), changes)
        self.assertNotIn(("permission", "core.phone", "android.permission.POST_NOTIFICATIONS", "revoke"), changes)
        self.assertEqual(len(protected), 2)
        self.assertFalse(any(x[2] == "android.permission.READ_CONTACTS" for x in changes))

    def test_local_network_compatibility_grant_uses_appop_and_exception_restores_default(self):
        device = bound_device()
        device.profile["privacy"] = {"notifications": [], "exceptions": {}}
        current = "No operations.\nDefault mode: allow"
        def shell(*args):
            if args[:2] == ("pm", "list"):
                return "package:social.app"
            if args[:3] == ("cmd", "package", "query-activities"):
                return ""
            if args[:2] == ("dumpsys", "package"):
                return package_permissions({"ACCESS_LOCAL_NETWORK": (True, "REVOKE_WHEN_REQUESTED")})
            if args[:3] == ("cmd", "appops", "get"):
                return current
            raise AssertionError(args)
        with patch.object(device, "shell", side_effect=shell):
            changes, _, _ = device.privacy_changes()
            self.assertEqual(changes, [("appop", "social.app", "ACCESS_LOCAL_NETWORK", "ignore")])
            current = "Uid mode: ACCESS_LOCAL_NETWORK: ignore"
            self.assertEqual(device.privacy_changes()[0], [])
            device.profile["privacy"]["exceptions"]["social.app"] = ["android.permission.ACCESS_LOCAL_NETWORK"]
            self.assertEqual(device.privacy_changes()[0], [("appop", "social.app", "ACCESS_LOCAL_NETWORK", "default")])

    def test_denied_permissions_are_pinned_against_android_default_regrants(self):
        device = bound_device()
        device.profile["privacy"] = {"notifications": [], "exceptions": {}}
        flags = ""
        def shell(*args):
            if args[:2] == ("pm", "list"): return "package:social.app"
            if args[:3] == ("cmd", "package", "query-activities"): return ""
            if args[:2] == ("dumpsys", "package"):
                return package_permissions({"CAMERA": (False, flags)})
            raise AssertionError(args)
        with patch.object(device, "shell", side_effect=shell):
            self.assertEqual(device.privacy_changes()[0], [("flags", "social.app", "android.permission.CAMERA", "deny")])
            flags = "USER_SET|USER_FIXED"
            self.assertEqual(device.privacy_changes()[0], [])

    def test_policy_backup_precedes_mutation_and_repeat_has_no_mutations(self):
        device = bound_device()
        change = ("permission", "social.app", "android.permission.CAMERA", "revoke")
        with tempfile.TemporaryDirectory() as tmp, patch("manage.ROOT", Path(tmp)):
            backup = Path(tmp) / ".local/backups/privacy.json"
            def mutate(*args):
                self.assertTrue(backup.is_file())
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            with patch.object(device, "privacy_changes", side_effect=[([change], [], {"before": True}), ([], [], {}), ([], [], {})]), \
                    patch.object(device, "shell", side_effect=mutate) as shell:
                device.reconcile_privacy()
                device.reconcile_privacy()
            self.assertEqual(shell.call_args_list, [
                call("pm", "revoke", "--user", "0", "social.app", "android.permission.CAMERA"),
                call("pm", "set-permission-flags", "--user", "0", "social.app", "android.permission.CAMERA", "user-set", "user-fixed")])
            self.assertEqual(json.loads(backup.read_text()), {"before": True})

    def test_home_uses_managed_patched_packages_and_configured_order(self):
        device = bound_device()
        device.profile["home"] = manage.read("devices.json")["dragoon"]["home"]
        with patch.object(device, "shell", side_effect=lambda *args: args[-1] + "/.MainActivity"):
            config = device.home_configuration()
        self.assertEqual([x["label"] for x in config["rows"][0]], ["Phone", "Messages", "Chrome", "Camera"])
        self.assertEqual(config["rows"][1][2]["component"], "com.instagram.android.hush/.MainActivity")
        self.assertEqual(config["rows"][2][0]["component"], "app.morphe.reddit/.MainActivity")
        self.assertEqual([x["label"] for x in config["dock"]], ["Waze", "Maps", "YT Music", "Spotify"])
        self.assertIn("com.reddit.frontpage", config["hidden"])
        device.profile["home"] = dict(device.profile["home"], dock=["music"])
        with self.assertRaisesRegex(ValueError, "four dock"):
            device.home_configuration()

    def test_bundle_verifies_identity_and_publisher_before_install(self):
        import zipfile
        device = bound_device()
        app = {"package": "com.spotify.music", "code": 1, "version": "1", "certificate": "a" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "spotify.apkm"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("base.apk", b"base")
                archive.writestr("../split.apk", b"split")
            with patch("manage.apk_identity", return_value=(app["package"], 1, "1")), \
                    patch("manage.run", return_value="Signer #1 certificate SHA-256 digest: " + "a" * 64), \
                    patch.object(device, "adb", return_value="Success") as adb:
                self.assertEqual(device.install_apk(bundle, app), "Success")
                args = adb.call_args.args
                self.assertEqual(args[:2], ("install-multiple", "-r"))
                self.assertEqual(len(args), 4)
                self.assertTrue(all(".." not in str(p) for p in args[2:]))
            with patch("manage.apk_identity", return_value=(app["package"], 1, "1")), \
                    patch("manage.run", return_value="Signer #1 certificate SHA-256 digest: " + "b" * 64), \
                    patch.object(device, "adb") as adb:
                with self.assertRaisesRegex(ValueError, "declared publisher"):
                    device.install_apk(bundle, app)
                adb.assert_not_called()

    def test_setup_applies_home_and_privacy_after_apps(self):
        device = Mock(profile={"apps": [], "home": {"rows": [["spotify"]], "dock": []},
                               "remove_stock": {"reddit": "com.reddit.frontpage"}, "links": {"reddit": ["reddit.com"]},
                               "privacy": {"configured": True}})
        with patch("sys.argv", ["manage.py", "setup", "dragoon"]), \
                patch("manage.Device", return_value=device), patch("manage.artifact", side_effect=lambda name, app, **kw: app):
            manage.main()
        self.assertEqual([c[0] for c in device.mock_calls],
                         ["verify", "reconcile", "reconcile_home", "reconcile_stock_duplicates", "reconcile_links", "reconcile_privacy"])

    def test_stock_removal_requires_all_replacements_and_update_inputs_then_is_idempotent(self):
        device = bound_device()
        device.profile["remove_stock"] = {"reddit": "com.reddit.frontpage", "instagram": "com.instagram.android"}
        apps = manage.read("apps.json")
        with tempfile.TemporaryDirectory() as tmp, patch("manage.ROOT", Path(tmp)), \
                patch("manage.read", return_value=apps), patch("manage.artifact", side_effect=lambda name, app: app), \
                patch.object(device, "drift", return_value=[]) as drift, patch.object(device, "shell") as shell:
            inputs = Path(tmp) / ".local/inputs"
            inputs.mkdir(parents=True)
            key = inputs.parent / "morphe.keystore"
            key.write_bytes(b"retained key")
            installed = set(device.profile["remove_stock"].values())
            system = {"com.instagram.android"}
            disabled, mutations = set(), []
            def remove(*args):
                if args[:3] == ("pm", "list", "packages"):
                    packages = system if "-s" in args else disabled
                    return "\n".join(f"package:{p}" for p in packages)
                mutations.append(args)
                if args[:4] == ("pm", "disable-user", "--user", "0"):
                    disabled.add(args[4])
                    return "new state: disabled-user"
                self.assertEqual(args[:4], ("pm", "uninstall", "--user", "0"))
                installed.remove(args[4])
                return "Success"
            shell.side_effect = remove
            with patch.object(device, "version", side_effect=lambda package: 1 if package in installed else None):
                with self.assertRaisesRegex(RuntimeError, "verified source APK and signing key"):
                    device.reconcile_stock_duplicates()
                self.assertEqual(mutations, [])
                for name in device.profile["remove_stock"]:
                    source = inputs / apps[name]["build"]["file"]
                    source.write_bytes(b"retained original")
                    apps[name]["build"]["sha256"] = manage.checksum(source)
                source.write_bytes(b"changed original")
                with self.assertRaisesRegex(RuntimeError, "verified source APK"):
                    device.reconcile_stock_duplicates()
                self.assertEqual(mutations, [])
                source.write_bytes(b"retained original")
                drift.side_effect = [[], ["patched APK differs"]]
                with self.assertRaisesRegex(RuntimeError, "replacement is not in sync"):
                    device.reconcile_stock_duplicates()
                self.assertEqual(mutations, [])
                drift.side_effect = None
                key.unlink()
                with self.assertRaisesRegex(RuntimeError, "signing key"):
                    device.reconcile_stock_duplicates()
                self.assertEqual(mutations, [])
                key.write_bytes(b"retained key")
                device.reconcile_stock_duplicates()
                device.reconcile_stock_duplicates()
            self.assertEqual(mutations, [
                ("pm", "uninstall", "--user", "0", "com.reddit.frontpage"),
                ("pm", "disable-user", "--user", "0", "com.instagram.android")])
            self.assertEqual(key.read_bytes(), b"retained key")
            self.assertTrue(all((inputs / apps[name]["build"]["file"]).read_bytes() == b"retained original"
                                for name in device.profile["remove_stock"]))

    def test_stock_mapping_cannot_remove_a_managed_package(self):
        device = bound_device()
        device.profile["remove_stock"] = {"reddit": "app.morphe.reddit"}
        with patch.object(device, "shell") as shell:
            with self.assertRaisesRegex(ValueError, "Invalid stock removal mapping"):
                device.reconcile_stock_duplicates()
        shell.assert_not_called()

    def test_check_reports_stock_duplicates_without_removing_them(self):
        device = Mock(profile={"apps": [], "remove_stock": {"reddit": "com.reddit.frontpage"}})
        device.stock_duplicates.return_value = {"reddit": "com.reddit.frontpage"}
        with patch("sys.argv", ["manage.py", "check", "dragoon"]), patch("manage.Device", return_value=device):
            self.assertEqual(manage.main(), 1)
        self.assertEqual([c[0] for c in device.mock_calls], ["verify", "stock_duplicates"])
