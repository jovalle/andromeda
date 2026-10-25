# Andromeda

Personal Android infrastructure, driven by `just`. JSON declares the desired
state; ADB verifies device identity, reconciles drift, and checks the result.
Python's standard library handles orchestration. No root or Python dependencies.

**Dragoon** is a Pixel 10 Pro XL. Its profile manages:

| App                  | Version                 | Source                                                       |
| -------------------- | ----------------------- | ------------------------------------------------------------ |
| Coffee               | 2.27                    | F-Droid, version code 47                                     |
| Morphe Manager       | 2.2.0                   | MorpheApp release                                            |
| MicroG               | 7.2.2                   | MorpheApp/MicroG-RE, arm64                                   |
| YouTube Morphe       | 21.20.405               | Official patches 1.48.0                                      |
| YouTube Music Morphe | 9.20.53                 | Official patches 1.48.0                                      |
| HushGram             | Instagram 452.1.0.53.64 | HushGram patches 0.0.16                                      |
| Reddit Morphe        | 2026.24.0               | Official patches 1.48.0                                      |
| Facebook             | 584.0.0.51.56           | HushFacebook patches 0.18.0                                  |
| Messenger            | 584.0.0.49.98           | HushMessenger patches 0.26.0                                 |
| TikTok               | 47.5.3                  | HushFeed patches 0.79.0                                      |
| Waze                 | 5.25.0.1                | Forsigh patches 0.30.41                                      |
| Twitter              | 12.19.1-release.0       | Piko patches 3.10.0                                          |
| rif                  | 5.6.22                  | Patcheddit 1.4.0; requires local OAuth settings              |
| RedReader            | 1.27                    | Official GitHub release; OAuth setup helper                  |
| Spotify              | 9.1.94.1912             | Publisher-signed bundle; certificate checked against Spotify |

YouTube, Music, Instagram, and Reddit install beside their stock apps. Instagram explicitly enables
**Install next to Instagram**, under `com.instagram.android.hush`. Reddit enables
**Clone app** under `app.morphe.reddit`; it needs no rif developer key. These
separate package names allow verification before removing the stock copies.
The other five additions
use their original package names and were absent before deployment. Existing
copies with a different signing key require the usual destructive-reinstall
confirmation. Patch selections use the pinned bundles' defaults, with the two
cloning patches above. In-app preferences keep their defaults;
account sign-in stays on the phone.

Dragoon's initial setup installs **Andromeda Home**, a small native launcher
configured directly over ADB. Its grid and dock contain the configured apps;
there are no widgets or previous home/dock shortcuts. Pixel Launcher and its
existing layout remain available with `just pixel dragoon`.

Setup disables preloaded stock YouTube and Music and uninstalls stock Instagram
and Reddit for owner user 0
after verifying all their patched replacements, retained source APK hashes,
and the persistent signing key. `devices.json` explicitly maps these removals
under `remove_stock`. Uninstalling erases a stock copy's separate app data;
patched copies and their data are preserved. Android 17 blocks per-user removal
of system apps without root, so system copies are disabled with their data retained.
Other Android profiles are untouched. Repeated setup removes or disables a stock
copy again if it is reinstalled or re-enabled; `check` reports drift without changes.

Installed stock copies are not required to run or update the patched copies.
Keep `.local/inputs/` and `.local/morphe.keystore` for future builds. Patched
apps update through reviewed Andromeda pins and builds signed with the same
local key; Play Store does not update these patched copies.

On a fresh device, YouTube and Music may each ask for one app restart on first
launch. Accept it, then sign in if desired. HushGram has its own sign-in because
its app data is separate from stock Instagram.

## Conversations

```sh
just setup dragoon               # Build, install, and reconcile all declared apps
just check dragoon               # Read-only report; exit 1 when there is drift
just install dragoon youtube     # Reconcile one app, building if necessary
just install dragoon music
just install dragoon instagram
just install dragoon reddit
just install dragoon facebook
just install dragoon messenger
just install dragoon tiktok
just install dragoon waze
just install dragoon twitter
just install dragoon rif         # Requires .local/rif.json first
just open dragoon redreader      # Complete OAuth setup on the phone
just install dragoon coffee      # Install/enable Coffee and keep the screen awake
just update dragoon              # Reconcile the pins currently in the repo
just open dragoon youtube
just open dragoon music
just open dragoon instagram
just open dragoon reddit         # The same pattern opens any managed app
just open dragoon                # Open Morphe Manager
just yolo=true setup dragoon     # No repo prompts; use safe defaults
just home dragoon                # Install home apps and reconcile home/privacy
just privacy dragoon             # Reconcile notifications and runtime permissions
just pixel dragoon               # Return to the retained Pixel Launcher
just test                       # Offline tests; no device required
```

Repeated setup reuses verified builds, compares the installed APK's hash, and
avoids reinstalling apps when state matches. Changes to patches or options count
as drift even when the app version stays the same. Missing build artifacts are
reported as **build required** by `check`; it never builds or downloads.

`setup` also reconciles enabled state, notification permission, Manager's
permission to install patched APKs, and battery exemptions for Manager/MicroG.
It verifies the hardware serial, model, CPU ABI, and owner profile before
changing the device. No command picks the first connected device.

Before `setup`, `update`, `install` (for any app), or `open`, Andromeda installs
and enables [Coffee](https://f-droid.org/packages/com.github.muellerma.coffee/)
first, applies the configured notification policy, and exempts it from battery optimization.
If its screen wake lock is absent, Andromeda wakes the display, adds Coffee's
Quick Settings tile, and clicks it once. It verifies the actual wake lock before
continuing with other apps or builds. Repeated runs leave active Coffee on.
`check` reports inactive Coffee as drift without activating it; pairing and
transport-selection commands do not provision apps.

The same modifying commands restore Dragoon's Quick Settings to two rows of
single-unit tiles, removing all others:

| Wi-Fi       | Bluetooth     | Mobile data | Airplane mode |
| ----------- | ------------- | ----------- | ------------- |
| Modes (DND) | Battery Saver | Auto-rotate | Coffee        |

The ordered tile list lives in `devices.json`. Android's `set-large-tiles`
command clears all large-tile preferences each run. `check` audits tile order;
it cannot read SystemUI's private size preferences. Sizing is verified through
the command's response, and this layout was visually checked on Dragoon's
Android 17 build. Tile layout changes preserve the toggles' current states.

Coffee stays on after the command finishes. Turn it off with its tile
or the power button; the next provisioning/open command enables it
again. Its default mode has no timeout. Leave that mode selected and the phone
unlocked for activation; private Coffee preferences are not rewritten. Keeping
the display awake uses extra battery. This is a startup check, not a watchdog:
manually switching the screen off or setting a Coffee timeout can stop it during
a run.

## Initial home and privacy setup

`just setup dragoon` (and `update`) installs missing declared apps, restores any
missing preloaded home apps for owner user 0, builds/installs Andromeda Home,
selects it as the default launcher, and applies the permission policy. No screen
gestures, widget dragging, launcher database edits, or root are involved.
`just home dragoon` applies the same home and privacy setup without reconciling
managed apps that are absent from the home layout.
Both commands remove configured stock duplicates after the home apps are in sync
and before applying privacy restrictions. Full setup also applies link defaults.

`links` in `devices.json` assigns supported web domains to patched apps for owner
user 0. Setup enables Android's link handling and selects only the configured
domains after checking that the installed app declares them. This uses the same
per-user choice as **Open supported links**, without changing website verification
results. `check` reports drift without writing; repeated setup leaves matching
choices alone. Updates reapply choices after installing replacements.

Reddit links use Reddit Morphe; rif remains an alternate client. YouTube and
YT Music own their respective domains. Facebook links use Facebook, while
Messenger owns messaging domains such as `m.me` and `messenger.com`. Google Maps
domains remain outside Waze's assignments. URL paths must still match the app's
intent filters, and embedded browsers can keep links inside their own app.
Facebook disables its general profile-link alias, and Android prevents ADB from
re-enabling that component. `facebook.build.profile_links` adds profile intent
filters directly to the existing public `IntentUriHandler` and enables its
manifest default. This covers username, numeric-ID, and `/people/name/id` paths
on Facebook domains already declared by the app. The added filter excludes known
login, logout, consent, checkpoint, dialog, and OAuth endpoints. Existing
authentication and post/reel/group filters remain intact.

`patches/FacebookProfileLinks.java` edits only the compiled manifest, using the
library bundled with the pinned Morphe CLI. After normal HushFacebook patching,
Andromeda adds this fix, aligns native libraries for 16 KiB pages, and signs again
with the existing host key. The source file's hash participates in Facebook's
build cache identity, along with the verified HushFacebook base APK hash. The
normal HushFacebook build is reused when available. Setup installs the resulting APK with data-preserving
replacement and reapplies the configured supported-link choices.
On Dragoon, username and numeric-ID links were visually verified to open the
profile inside Facebook, including after a force-stop and cold link launch.

`devices.json` declares ordered `home.rows`, four `home.dock` entries, labels,
preloaded `home.system` packages, and hidden stock packages. References to
`apps.json` use the managed version, so `instagram`, `reddit`, `youtube`, and
`music` choose the patched copies. The initial layout is:

| Phone    | Messages  | Chrome         | Camera |
| -------- | --------- | -------------- | ------ |
| Facebook | Messenger | Instagram      | TikTok |
| Reddit   | rif       | YouTube Morphe |        |

Dock: Waze, Maps, YT Music, Spotify. **All apps** opens the remaining installed
apps and Settings; long-pressing an icon opens its Android App info. The launcher
requires no network or sensitive runtime permissions. Its configuration provider
checks Android's DUMP permission and the caller's shell/root UID before accepting
validated, installed app components. Configuration is stored privately and read
back after writes. Its APK is built locally from `home/` using the Android SDK
and the existing host signing key.

`privacy.notifications` allows Phone, Messages, Safety Hub, and Play Store;
essential Android services are exempt. Notifications are revoked from other
user-facing and third-party apps, including managed apps. Android may still
show exempt media/call controls and active foreground services. Coffee continues
to work through its Quick Settings tile with notifications denied; its own app
screen may request notification permission.

Other Android runtime permissions are revoked unless listed in `privacy.allow`
or per-package `privacy.exceptions`. Approved exceptions retain Phone/Messages
calling and contacts, Camera camera/microphone, and foreground location for
Maps/Waze, plus the approved Photos media-access exception. These explicit exceptions grant the declared permissions, including
foreground navigation location; background location stays denied. The system photo picker may retain access to specifically
selected media. Privacy also hides private notification content on the lock
screen and enables clipboard-access alerts. Optional permissions for social
apps, Chrome, and Spotify are denied; add an explicit exception and rerun setup
to allow a feature you decide to use later.

Android 17 local-network compatibility grants are constrained through the
UID app operation because Android can retain the nominal permission grant.
Denied permissions are marked user-set/user-fixed so default role grants and
app prompts cannot silently undo them. Adding an explicit exception clears the
fixed denial and grants that declared permission. Android-fixed grants are
reported and preserved. Photos keeps its five media permissions because its
system-gallery role restores them after package-install events; keeping this
exception preserves Camera's gallery/review integration. This policy does not edit
private in-app tracking settings or block Internet access. Before the first
permission change, the original grant state is saved in ignored
`.local/backups/privacy.json`; the previous launcher is saved beside it in
`home.json`. `check` audits home configuration and permission drift without
changing them. Repeated setup applies no permission changes when already in sync.

Spotify's original, publisher-signed bundle is pinned in `apps.json` and retained
at `.local/inputs/spotify.apkm`. Missing Spotify installs with `adb install-multiple`
after checking the bundle hash, base package/version, and every APK's publisher
certificate. Keep that input with the other private original APKs when moving
this checkout to another host. Andromeda does not automate Play Store account
login or silently substitute third-party patched Spotify APKs.

## Host setup

For prebuilt APK provisioning: Python 3.11+, [just](https://just.systems/), and
current Android [Platform Tools](https://developer.android.com/tools/releases/platform-tools).
Building patched apps and Andromeda Home also requires Java 21+ and Android SDK Build Tools
(`aapt`, `apksigner`, `d8`, `zipalign`) and SDK Platform 37 for Andromeda Home. `ANDROID_HOME` or `ANDROID_SDK_ROOT` selects the SDK;
the macOS fallback is `~/Library/Android/sdk`.

On macOS:

```sh
brew install just python openjdk@21
brew install --cask android-platform-tools
# Install Android SDK Build Tools if they are not already installed.
```

The reconciler finds Homebrew's Java 21 directly, without a system Java symlink.
Set `JAVA_HOME` to use another JDK. Morphe CLI 1.19.1 and the patch bundles are
pinned in `builds.json`, downloaded on demand, and verified before execution.

Enable Developer options and USB debugging on the phone, connect it, unlock it,
and accept **Allow USB debugging**. Run `just devices` and put its USB serial in
ignored `devices.local.json`:

```json
{ "dragoon": { "serial": "YOUR_USB_SERIAL" } }
```

This checkout is already bound to Dragoon locally; its serial is not committed.

## Original app inputs

YouTube and Music use supported stable versions rather than forcing patches onto
newer versions from the Play Store. Download the original, unmodified APKs from
the pages recorded in `apps.json` and save them as:

```text
.local/inputs/youtube.apk       # 21.20.405, universal nodpi APK
.local/inputs/music.apk         # 9.20.53, arm64-v8a nodpi APK
.local/inputs/reddit.apkm       # 2026.24.0, complete original bundle
.local/inputs/facebook.apkm     # 584.0.0.51.56, build 475817393
.local/inputs/messenger.apk     # 584.0.0.49.98, build 346814894
.local/inputs/tiktok.apk        # 47.5.3, plain APK (not the APKM variant)
.local/inputs/waze.apkm         # 5.25.0.1, arm64-v8a bundle
.local/inputs/twitter.apkm      # 12.19.1-release.0, complete original bundle
.local/inputs/rif.apk           # 5.6.22, original TalkLittle APK
```

They are already present in this checkout. Their hashes and publisher signing
certificates are pinned and checked before patching. APKMirror's download flow
requires a browser; the reconciler deliberately does not scrape it or substitute
unreviewed prepatched APKs.

Instagram's original APK set is captured from Dragoon's existing Play Store
installation when `.local/inputs/instagram.apkm` is absent. Capture requires the
exact pinned version/code, uses deterministic ZIP metadata, and checks the
bundle hash before accepting it. Every split's publisher signature is checked
before patching. If Play Store updates the original, restore the reviewed input
from a private backup or review and pin a new compatible version.
Once setup removes stock Instagram, rebuilding uses the retained input; restore
that file from backup if it is missing.

## Build and install

The official [Morphe Desktop CLI](https://github.com/MorpheApp/morphe-desktop)
performs local builds. Default patches come from
[MorpheApp/morphe-patches](https://github.com/MorpheApp/morphe-patches).
Instagram uses [SysAdminDoc/HushGram](https://github.com/SysAdminDoc/HushGram),
a separate community project supporting the pinned Instagram build. It is not
part of Morphe's official patch bundle.

Facebook uses [HushFacebook](https://github.com/SysAdminDoc/HushFacebook),
Messenger uses [HushMessenger](https://github.com/SysAdminDoc/HushMessenger),
TikTok uses [HushFeed](https://github.com/SysAdminDoc/HushFeed), and Twitter uses
[Piko](https://github.com/crimera/piko). Waze uses
[Forsigh's Waze patches](https://github.com/Forsigh/waze-patches), listed in the
[Morphe Community catalog](https://morphe-patches.software/?app=com.waze#apps).
These are independent community projects. Waze's experimental and Android Auto
patches remain off at their defaults; phone startup checks do not test driving
guidance or car integration.

Builds use full bytecode mode, retain arm64 libraries, stop on patch errors, and
verify the output package, version/code, and APK signature before installation.
A successful build receipt, its APK hash, and logs live in `.cache/builds/`.
The build identity includes the declared app, CLI and bundle pins, build mode,
ABI, and signing key. Only the bundles used by that app affect its build identity,
so adding or updating another app's source does not rebuild unrelated apps.
Messenger declares its main launcher explicitly because Hush adds a second
settings launcher. Changing a launcher does not change the APK build identity.
Cached APKs are verified again before reuse. Output identity is read directly
from the compiled manifest, without resolving newer Android platform icons.

The Reddit signature patch and Piko's default **Change version code** patch set
the output version code to `2147483647` to prevent Play Store replacement.
`apps.json` records that output code separately from the original input code in
`build.code`. Future builds retain the output code and signing key for updates
that preserve data. An original app being older does not mean the output's
version code should be lowered.
Waze's default skin-refresh dependency also raises its output code, from
`1030736` to `1030756`, so the app re-extracts its patched resources. Both codes
are pinned separately; this does not enable the optional Play Store lock patch.

Updates use `adb install -r` and preserve app data. Newer installed versions are
reported as drift and preserved; downgrades are refused. A signature conflict
requires explicit interactive confirmation before uninstalling and erasing its
data across Android profiles.
The default is **No**, including in YOLO or noninteractive runs. Uninstalling
Morphe Manager also erases its own signing keystore. There are no automatic
retries that erase data.

YOLO controls repo prompts. It does not bypass Android's USB trust, wireless
pairing, account login, or dialogs in Morphe Manager.

## rif OAuth setup

rif uses [Patcheddit](https://github.com/wchill/patcheddit), which requires a
working Reddit OAuth client ID. This value stays out of Git. The original
TalkLittle APK and the patch bundle are pinned, but rif cannot be built or
deployed until its OAuth settings are supplied.

For the RedReader route, run `just open dragoon redreader`, review Reddit's user
agreement on the phone, and add your Reddit account. Reddit's authorization email
contains an **App ID**. Put only that ID in `.local/rif.json`:

```json
{ "client-id": "YOUR_APP_ID" }
```

Then run `just install dragoon rif` and `just open dragoon rif`. The profile uses
Patcheddit's matching defaults: redirect URI `redreader://rr_oauth_redir` and user
agent `org.quantumbadger.redreader/1.25.1`. To use a separately approved OAuth app,
add its matching `redirect-uri` and `user-agent` to the same private JSON file.
Do not provide a client secret, password, email contents, or access token.

Private options are included in the build identity, so changing them requires a
new build. Missing settings report **build required** in `check`; install/setup
fail with an actionable message, including in YOLO mode. Patch logs, generated
APKs and `.local/` are ignored because a patched APK embeds its client ID.
RedReader and the existing official Reddit apps remain installed; removing them
is a separate decision because it would erase their local data.

On Dragoon, Play Protect blocks rif's older Android target and offers an
**Install anyway** choice. This requires a separate, explicit decision on the
phone; YOLO does not approve security exceptions or disable Play Protect.

## Signing key and backups

**Back up `.local/morphe.keystore` privately.** It is the persistent host signing
key for all managed patched apps. `.local/` is protected by directory permissions,
the key is mode 0600, and both are ignored by Git. It is a PKCS12 keystore with
alias `morphe` and store/key password `Morphe`; the password is a compatibility
default, so protect the file and encrypt off-machine backups.

Do not delete `.local/` when cleaning build caches. `.cache/` can be rebuilt from
the retained originals and key. The reconciler refuses to regenerate a missing
key when build receipts still exist. If both the key and receipts are lost,
Android's signature checks still prevent replacement of existing apps without a
destructive reinstall.

For updates, use `just`. If you want to repatch these apps in Android Morphe
Manager, import this host key there first; Manager otherwise has a different
key. Its signing-key import may need the alias/password above. Manager's own
[backup guide](https://github.com/MorpheApp/morphe-manager/blob/v2.2.0/docs/backup-and-keystore.md)
explains key and settings exports. Private app preferences are not accessible
through ordinary non-root ADB; this repo does not pretend to reconcile them.

Keep settings exports private too: they can contain a GitHub token. `backups/`,
`*.keystore`, `.local/`, APK caches, and device bindings are ignored by Git.

## Wireless ADB

Use Android's **Wireless debugging** screen on a trusted network:

```sh
just pair dragoon 192.168.0.208:37123
# Enter the pairing code at ADB's prompt.
just connect dragoon 192.168.0.208:40567
just setup dragoon
just usb dragoon
```

The pairing and connection ports differ. `connect` verifies the endpoint's
hardware identity before saving it locally. After a reboot or Wi-Fi change,
connect with the current address. `usb` selects the original USB serial again.
These commands use authenticated Wireless debugging, without enabling port 5555.

## Changing desired state

- `devices.json`: device identity, apps, home layout, tiles, privacy, and stock removal mappings.
- `devices.local.json`: private hardware serials and current ADB transports.
- `apps.json`: package, version/code, original input identity, patch choices, and permissions.
- `builds.json`: exact CLI and patch bundle URLs and release SHA-256 digests.
- `manage.py`: reconciliation; `justfile`: public interface.

To upgrade, review the upstream release and compatibility, update the relevant
pins, and run `just update dragoon`. `update` never silently follows latest.
For GitHub artifacts, use the release asset's published SHA-256 `digest`.
Coffee uses the exact APK URL and SHA-256 from F-Droid's repository index.
Inspect APK package/versionCode with `aapt dump badging`. Check source publisher
certificates with `apksigner verify --print-certs` before changing an original
input pin. Checksums bind bytes to the reviewed release; they do not constitute
an independent audit of third-party code.
