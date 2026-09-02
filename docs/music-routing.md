# Music Routing on macOS

BlackHole carries a silent copy of system audio to the laptop companion. The
Pi remains the only process that draws and sends WLED frames.

1. Install BlackHole 2ch and LedFx.
2. Open **Audio MIDI Setup** and create a Multi-Output Device containing the
   listening output and BlackHole 2ch.
3. Make the listening output the primary device. Enable drift correction on
   BlackHole.
4. Give each useful combination a descriptive name, such as `AirPods + WLED`
   or `Laptop Speakers + WLED`.
5. Run `bash music-companion/install-macos.sh` once. It starts at login.
6. Open `http://raspberrypi.local:8090`, choose the speaker and light style,
   and select **Start music lights**.

The listening device can change while LedFx continues to use BlackHole. A
separate Multi-Output Device is usually needed for each speaker/headphone
combination. AirPlay and HomePod routing may disconnect or remain silent in a
Multi-Output Device because AirPlay adds buffering and does not behave like a
normal local hardware output.

The control panel changes the macOS output for you. It offers Pulse, Prism,
Spectrum, Lava, Comets, and Aurora. Stop only removes the music-reactive layer;
it does not interrupt playback or change the selected speaker.

LedFx 2.1.5 is currently retained as a hidden audio-analysis engine because it
already has macOS audio permission. It drives a private dummy device, not the
real WLED controller. The companion subscribes only to frequency data and sends
compact bass, midrange, treble, energy, and beat values to the Pi. If the Pi or
Wi-Fi temporarily disappears, analysis stays alive and resumes delivery when
the renderer returns.
