#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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

static int uid_for_device(AudioDeviceID device, char *buffer, size_t length) {
    AudioObjectPropertyAddress address = {
        kAudioDevicePropertyDeviceUID,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    CFStringRef uid = NULL;
    UInt32 size = sizeof(uid);
    if (AudioObjectGetPropertyData(device, &address, 0, NULL, &size, &uid) != noErr || !uid) {
        return 0;
    }
    int ok = CFStringGetCString(uid, buffer, (CFIndex)length, kCFStringEncodingUTF8);
    CFRelease(uid);
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

static AudioDeviceID device_for_uid(const char *uid, AudioDeviceID *devices, UInt32 count) {
    for (UInt32 index = 0; index < count; index++) {
        char candidate[512];
        if (uid_for_device(devices[index], candidate, sizeof(candidate)) && strcmp(uid, candidate) == 0) {
            return devices[index];
        }
    }
    return kAudioObjectUnknown;
}

static AudioDeviceID device_for_name(const char *name, AudioDeviceID *devices, UInt32 count) {
    for (UInt32 index = 0; index < count; index++) {
        char candidate[512];
        if (has_output(devices[index]) && name_for_device(devices[index], candidate, sizeof(candidate)) && strcmp(name, candidate) == 0) {
            return devices[index];
        }
    }
    return kAudioObjectUnknown;
}

static CFDictionaryRef subdevice_description(const char *uid, int drift_correction) {
    CFStringRef uid_ref = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    if (!uid_ref) return NULL;
    CFMutableDictionaryRef description = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (description) {
        CFDictionarySetValue(description, CFSTR(kAudioSubDeviceUIDKey), uid_ref);
        CFDictionarySetValue(description, CFSTR(kAudioSubDeviceDriftCompensationKey), drift_correction ? kCFBooleanTrue : kCFBooleanFalse);
    }
    CFRelease(uid_ref);
    return description;
}

static int repair_route(const char *route_name, const char *speaker_name, AudioDeviceID *devices, UInt32 count) {
    AudioDeviceID speaker = device_for_name(speaker_name, devices, count);
    AudioDeviceID blackhole = device_for_name("BlackHole 2ch", devices, count);
    if (speaker == kAudioObjectUnknown) {
        fprintf(stderr, "Speaker is not connected: %s\n", speaker_name);
        return 0;
    }
    if (blackhole == kAudioObjectUnknown) {
        fputs("BlackHole 2ch is not available.\n", stderr);
        return 0;
    }
    char speaker_uid[512], blackhole_uid[512];
    if (!uid_for_device(speaker, speaker_uid, sizeof(speaker_uid)) || !uid_for_device(blackhole, blackhole_uid, sizeof(blackhole_uid))) {
        fputs("Could not identify the selected audio devices.\n", stderr);
        return 0;
    }

    AudioDeviceID current = default_output();
    AudioDeviceID existing = device_for_name(route_name, devices, count);
    if (existing != kAudioObjectUnknown) {
        if (existing == current) set_output(speaker);
        OSStatus destroy_status = AudioHardwareDestroyAggregateDevice(existing);
        if (destroy_status != noErr) {
            fprintf(stderr, "Could not replace the old route (CoreAudio %d).\n", (int)destroy_status);
            return 0;
        }
        // CoreAudio destroys aggregate devices asynchronously.
        usleep(300000);
    }

    char route_uid[512] = "com.michaelvoth.ambient-wled.";
    size_t offset = strlen(route_uid);
    for (const char *cursor = route_name; *cursor && offset + 1 < sizeof(route_uid); cursor++) {
        char character = *cursor;
        route_uid[offset++] = (character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') || (character >= '0' && character <= '9') ? character : '-';
    }
    route_uid[offset] = '\0';
    CFStringRef name_ref = CFStringCreateWithCString(NULL, route_name, kCFStringEncodingUTF8);
    CFStringRef uid_ref = CFStringCreateWithCString(NULL, route_uid, kCFStringEncodingUTF8);
    CFStringRef main_ref = CFStringCreateWithCString(NULL, speaker_uid, kCFStringEncodingUTF8);
    CFDictionaryRef speaker_description = subdevice_description(speaker_uid, 0);
    CFDictionaryRef blackhole_description = subdevice_description(blackhole_uid, 1);
    const void *members[] = {speaker_description, blackhole_description};
    CFArrayRef member_list = CFArrayCreate(NULL, members, 2, &kCFTypeArrayCallBacks);
    CFMutableDictionaryRef description = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (!name_ref || !uid_ref || !main_ref || !speaker_description || !blackhole_description || !member_list || !description) {
        fputs("Could not allocate the audio route description.\n", stderr);
        if (description) CFRelease(description); if (member_list) CFRelease(member_list);
        if (speaker_description) CFRelease(speaker_description); if (blackhole_description) CFRelease(blackhole_description);
        if (name_ref) CFRelease(name_ref); if (uid_ref) CFRelease(uid_ref); if (main_ref) CFRelease(main_ref);
        return 0;
    }
    CFDictionarySetValue(description, CFSTR(kAudioAggregateDeviceNameKey), name_ref);
    CFDictionarySetValue(description, CFSTR(kAudioAggregateDeviceUIDKey), uid_ref);
    CFDictionarySetValue(description, CFSTR(kAudioAggregateDeviceSubDeviceListKey), member_list);
    CFDictionarySetValue(description, CFSTR(kAudioAggregateDeviceMainSubDeviceKey), main_ref);
    CFDictionarySetValue(description, CFSTR(kAudioAggregateDeviceClockDeviceKey), main_ref);
    AudioDeviceID created = kAudioObjectUnknown;
    OSStatus status = AudioHardwareCreateAggregateDevice(description, &created);
    CFRelease(description); CFRelease(member_list); CFRelease(speaker_description); CFRelease(blackhole_description);
    CFRelease(name_ref); CFRelease(uid_ref); CFRelease(main_ref);
    if (status != noErr) {
        fprintf(stderr, "CoreAudio could not create the route (CoreAudio %d).\n", (int)status);
        return 0;
    }
    printf("Rebuilt %s with %s + BlackHole 2ch.\n", route_name, speaker_name);
    return 1;
}

static void print_device_name(AudioDeviceID device) {
    char name[512] = "unknown";
    name_for_device(device, name, sizeof(name));
    printf("%u\t%s", device, name);
}

static int inspect_aggregate(AudioDeviceID device, AudioDeviceID *devices, UInt32 count) {
    char name[512] = "unknown";
    name_for_device(device, name, sizeof(name));
    printf("Route: %s\n", name);

    AudioObjectPropertyAddress members_address = {
        kAudioAggregateDevicePropertyFullSubDeviceList,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    CFArrayRef members = NULL;
    UInt32 size = sizeof(members);
    if (AudioObjectGetPropertyData(device, &members_address, 0, NULL, &size, &members) != noErr || !members) {
        puts("This route is not an inspectable Multi-Output Device.");
        return 0;
    }
    puts("Configured members:");
    for (CFIndex index = 0; index < CFArrayGetCount(members); index++) {
        CFStringRef uid_ref = (CFStringRef)CFArrayGetValueAtIndex(members, index);
        char uid[512] = "unknown";
        CFStringGetCString(uid_ref, uid, sizeof(uid), kCFStringEncodingUTF8);
        AudioDeviceID member = device_for_uid(uid, devices, count);
        printf("  %s\t", member == kAudioObjectUnknown ? "missing" : "present");
        if (member == kAudioObjectUnknown) {
            puts(uid);
        } else {
            print_device_name(member);
            puts("");
        }
    }
    CFRelease(members);

    AudioObjectPropertyAddress active_address = {
        kAudioAggregateDevicePropertyActiveSubDeviceList,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    size = 0;
    if (AudioObjectGetPropertyDataSize(device, &active_address, 0, NULL, &size) == noErr && size) {
        AudioDeviceID *active = malloc(size);
        if (active && AudioObjectGetPropertyData(device, &active_address, 0, NULL, &size, active) == noErr) {
            puts("Active members:");
            for (UInt32 index = 0; index < size / sizeof(AudioDeviceID); index++) {
                printf("  ");
                print_device_name(active[index]);
                puts("");
            }
        }
        free(active);
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
    if (argc == 3 && strcmp(argv[1], "inspect") == 0) {
        for (UInt32 index = 0; index < count; index++) {
            char name[512];
            if (name_for_device(devices[index], name, sizeof(name)) && strcmp(name, argv[2]) == 0) {
                int ok = inspect_aggregate(devices[index], devices, count);
                free(devices);
                return ok ? 0 : 1;
            }
        }
        fprintf(stderr, "Audio output not found: %s\n", argv[2]);
        free(devices);
        return 1;
    }
    if (argc == 4 && strcmp(argv[1], "repair") == 0) {
        int ok = repair_route(argv[2], argv[3], devices, count);
        free(devices);
        return ok ? 0 : 1;
    }
    free(devices);
    fputs("Usage: audio-route {list|set|inspect DEVICE_NAME|repair ROUTE_NAME SPEAKER_NAME}\n", stderr);
    return 2;
}
