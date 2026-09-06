---
name: lattice-verify
description: Build, install and drive Lattice on a connected device to verify a change end to end, including running a real sweep and cleaning up afterwards. Use whenever a change needs proving on hardware rather than in tests - camera behaviour, UI layout, or anything writing capture sessions.
---

# Verifying a change on the device

The interesting behaviour in this project is all hardware. Tests cover the maths; the device covers
everything else.

## Two rules that exist because they were learned the hard way

### Snapshot sessions before a run; delete only by exact name

A `tail -1` once deleted 86 MB of real capture data because the run being cleaned up had not
actually created a session. Never infer which directory you created.

```bash
adb shell ls /sdcard/Lattice/ | tr -d '\r' > /tmp/before.txt
# ... run the sweep ...
adb shell ls /sdcard/Lattice/ | tr -d '\r' | grep -vxF -f /tmp/before.txt   # exactly what you made
```

Then delete each returned name individually, matching it against what you expect. Iterate line by
line — a multi-line variable will not match a `case` pattern and the guard will (correctly) refuse.

### Guard every scripted tap on the focused package

An incoming call once arrived mid-automation and was answered by a blind tap. Check focus before
each interaction:

```bash
guard() {
  F=$(adb shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus)
  case "$F" in *hamster.lattice*) return 0;; *) echo "!! focus: $F"; return 1;; esac
}
```

If the guard trips, stop and re-launch rather than continuing.

## Driving the UI

```bash
snap() { guard || return 1
  adb shell uiautomator dump /sdcard/wd.xml >/dev/null 2>&1
  adb shell cat /sdcard/wd.xml > /tmp/cur.xml; }

tapl() { guard || return 1
  B=$(grep -o "text=\"$1\"[^/]*bounds=\"\[[0-9,]*\]\[[0-9,]*\]\"" /tmp/cur.xml | head -1 \
      | grep -o '\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]')
  [ -z "$B" ] && { echo "!! '$1' not found"; return 1; }
  X=$(echo "$B"|sed 's/\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]/\1 \3/'|awk '{print int(($1+$2)/2)}')
  Y=$(echo "$B"|sed 's/\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]/\2 \4/'|awk '{print int(($1+$2)/2)}')
  adb shell input tap $X $Y; sleep 2; snap; }
```

Gotchas found while using this:

- **`KEYCODE_BACK` on the main screen exits the app.** With a sheet open it closes the sheet; with a
  text field focused it closes the keyboard first, so it may take two presses.
- **Dismiss a sheet by tapping the scrim** (around `540 500` on a 1080×2340 screen), not by swiping.
- **`uiautomator dump` includes `content-desc`.** Searching for `Focus distance` matches the FOCUS
  tile's accessibility label even when the sheet is shut — match on something sheet-specific.
- **Screenshot rather than trust the dump** when something looks wrong: `adb exec-out screencap -p > shot.png`.

## Making a sweep small enough to iterate on

The default configuration is 810 frames (9 ISO × 9 shutter × 10 focus × Auto white balance), in PNG
at 2× scale. To pin an axis to one value, open it, switch to **List**, and type a single number.
ISO `400`, shutter `20` (milliseconds), focus `1.0` (diopters) gives a sweep of
`1 × 1 × 1 × <white balance count>`.

Note the default format is PNG at ~30 MB per full-resolution frame, so the pre-flight space check
bites much sooner than it did under JPEG. Switch Format to JPEG while iterating if space is tight.

**Testing the Zip button** needs roughly as much free space again as the session, since the archive
is written before the folder is removed. It refuses rather than filling the disk, so a space
failure there is the feature working, not a bug.

## After verifying

Delete the sessions you created, by exact name, and leave the app at defaults. A session you
archived with the Zip button is a `<name>.zip` file rather than a directory, so check which you are
removing:

```bash
adb shell rm -rf /sdcard/Lattice/<exact_session_name>      # directory
adb shell rm -f  /sdcard/Lattice/<exact_session_name>.zip  # archived
adb shell pm clear dev.hamster.lattice
adb shell pm grant dev.hamster.lattice android.permission.CAMERA
```

Report what you actually observed — manifest values, screenshots — not that it "should work".
