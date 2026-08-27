#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int name_for_device(AudioDeviceID device, char *buffer, size_t length) {
    AudioObjectPropertyAddress address = {
        kAudioObjectPropertyName,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    CFStringRef name = NULL;
    UInt32 size = sizeof(name);
    if (AudioObjectGetPropertyData(device, &address, 0, NULL, &size, &name) != noErr || !name) {
        return 0;
    }
    int ok = CFStringGetCString(name, buffer, (CFIndex)length, kCFStringEncodingUTF8);
    CFRelease(name);
    return ok;
}

static AudioDeviceID default_output(void) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    AudioDeviceID device = 0;
    UInt32 size = sizeof(device);
    AudioObjectGetPropertyData(kAudioObjectSystemObject, &address, 0, NULL, &size, &device);
    return device;
}

static int has_output(AudioDeviceID device) {
    AudioObjectPropertyAddress address = {
        kAudioDevicePropertyStreams,
        kAudioDevicePropertyScopeOutput,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    return AudioObjectGetPropertyDataSize(device, &address, 0, NULL, &size) == noErr && size > 0;
}

static AudioDeviceID *all_devices(UInt32 *count) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &address, 0, NULL, &size) != noErr) {
        return NULL;
    }
    AudioDeviceID *devices = malloc(size);
    if (!devices || AudioObjectGetPropertyData(kAudioObjectSystemObject, &address, 0, NULL, &size, devices) != noErr) {
        free(devices);
        return NULL;
    }
    *count = size / sizeof(AudioDeviceID);
    return devices;
}

static int set_output(AudioDeviceID device) {
    AudioObjectPropertySelector selectors[] = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioHardwarePropertyDefaultSystemOutputDevice
    };
    for (int index = 0; index < 2; index++) {
        AudioObjectPropertyAddress address = {
            selectors[index],
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain
        };
        UInt32 size = sizeof(device);
        if (AudioObjectSetPropertyData(kAudioObjectSystemObject, &address, 0, NULL, size, &device) != noErr) {
            return 0;
        }
    }
    return 1;
}

int main(int argc, char **argv) {
    UInt32 count = 0;
    AudioDeviceID *devices = all_devices(&count);
    if (!devices) {
        fputs("Could not read CoreAudio devices.\n", stderr);
        return 1;
    }
    AudioDeviceID current = default_output();
    if (argc == 2 && strcmp(argv[1], "list") == 0) {
        for (UInt32 index = 0; index < count; index++) {
            char name[512];
            if (has_output(devices[index]) && name_for_device(devices[index], name, sizeof(name))) {
                printf("%u\t%d\t%s\n", devices[index], devices[index] == current, name);
            }
        }
        free(devices);
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "set") == 0) {
        for (UInt32 index = 0; index < count; index++) {
            char name[512];
            if (has_output(devices[index]) && name_for_device(devices[index], name, sizeof(name)) && strcmp(name, argv[2]) == 0) {
                int ok = set_output(devices[index]);
                free(devices);
                if (ok) {
                    puts("ok");
                    return 0;
                }
                fputs("CoreAudio rejected the output change.\n", stderr);
                return 1;
            }
        }
        fprintf(stderr, "Audio output not found: %s\n", argv[2]);
        free(devices);
        return 1;
    }
    free(devices);
    fputs("Usage: audio-route {list|set DEVICE_NAME}\n", stderr);
    return 2;
}
