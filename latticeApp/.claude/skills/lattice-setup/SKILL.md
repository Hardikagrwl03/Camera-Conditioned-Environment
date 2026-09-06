---
name: lattice-setup
description: Get Lattice building, installed and running on a connected Android device from a clean checkout. Use when setting up the project for the first time, when the build fails after a move or rename, or when the app is installed but refuses to capture.
---

# Setting up Lattice

## 1. Check the toolchain

```bash
java -version          # need 17+, developed on 21
adb devices            # exactly one device, state "device"
```

If `adb` shows `unauthorized`, accept the USB debugging prompt on the phone.

## 2. Build and install

```bash
./gradlew :app:testDebugUnitTest    # 45 tests, all should pass
./gradlew :app:installDebug
```

`local.properties` must point at an Android SDK. It is gitignored, so a fresh clone needs it:

```bash
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
```

## 3. Grant the two permissions

The app needs `CAMERA` and `MANAGE_EXTERNAL_STORAGE`. `PermissionGate` presents a button for each
and re-checks on resume, so granting them in the app works. To skip the UI:

```bash
adb shell pm grant dev.hamster.lattice android.permission.CAMERA
adb shell appops set --uid dev.hamster.lattice MANAGE_EXTERNAL_STORAGE allow
```

`MANAGE_EXTERNAL_STORAGE` cannot always be granted over adb on every OEM build. If the app still
shows the permission gate, grant it through the in-app button.

## 4. Confirm it works

```bash
adb shell monkey -p dev.hamster.lattice -c android.intent.category.LAUNCHER 1
adb shell dumpsys window | grep -m1 mCurrentFocus     # expect dev.hamster.lattice/.MainActivity
```

The preview should be live and the tile grid should show eight settings.

## Troubleshooting

**Build fails right after moving or renaming the project directory.** Gradle's configuration cache
stores absolute paths. Clear the stale state and rebuild:

```bash
rm -rf .gradle build app/build .kotlin
./gradlew :app:assembleDebug
```

**Unit tests fail with a `RuntimeException: Stub!`.** A test touched an Android type. The JVM test
suite must stay free of them — `android.util.Range` and friends stub out to throwing shims. Move
the logic behind a pure function taking plain values, the way `SweepDefaults` exposes pure
generators alongside its `CameraCapabilities` overloads.

**The app starts but capture refuses with a space warning.** The pre-flight check estimates ~5 MB
per JPEG and ~30 MB per PNG frame. Shrink an axis to a single value (switch it to List, type one
number) or free space.

**Capture runs but nothing appears in `/sdcard/Lattice`.** All-files access is not actually granted.
Check with `adb shell appops get --uid dev.hamster.lattice MANAGE_EXTERNAL_STORAGE`.
