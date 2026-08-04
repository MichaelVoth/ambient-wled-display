# Music Routing on macOS

LedFx must hear the same audio sent to the listening device. BlackHole provides
the silent software input used by LedFx.

1. Install BlackHole 2ch and LedFx.
2. Open **Audio MIDI Setup** and create a Multi-Output Device containing the
   listening output and BlackHole 2ch.
3. Make the listening output the primary device. Enable drift correction on
   BlackHole.
4. Give each useful combination a descriptive name, such as `AirPods + WLED`
   or `Laptop Speakers + WLED`.
5. Select that output from macOS Control Center.
6. Run `./ledfx/ledfx-control.sh energy` once LedFx scenes have been created.

The listening device can change while LedFx continues to use BlackHole. A
separate Multi-Output Device is usually needed for each speaker/headphone
combination. AirPlay and HomePod routing may disconnect or remain silent in a
Multi-Output Device because AirPlay adds buffering and does not behave like a
normal local hardware output.

For a no-terminal workflow, add the control script to a macOS Shortcut or
Login Item. Starting it automatically does not force the system output to a
particular speaker; selecting the named Multi-Output Device remains an explicit
choice.
