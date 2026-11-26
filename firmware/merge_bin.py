# Writes firmware-merged.bin (bootloader + partitions + app) for one-shot flashing at 0x0 with esptool-js.
import os

Import("env")


def merge(source, target, env):
    build = env.subst("$BUILD_DIR")
    images = [env.subst(str(x)) for x in env.Flatten(env.get("FLASH_EXTRA_IMAGES", []))]
    esptool = env.PioPlatform().get_package_dir("tool-esptool") + "/esptool"  # .exe on Windows
    cmd = [
        f'"{esptool}"', "--chip", "esp32s3", "merge-bin",
        "-o", f'"{build}/firmware-merged.bin"', "--flash-mode", "keep", "--flash-size", "16MB",
    ] + [f'"{x}"' for x in images] + ["$ESP32_APP_OFFSET", f'"{build}/firmware.bin"']
    # Wokwi only: bake the test filesystem (pio run -e wokwi -t buildfs) into the image at the littlefs offset
    fs = os.path.join(build, "littlefs.bin")
    if env["PIOENV"] == "wokwi" and os.path.exists(fs):
        cmd += ["0x610000", f'"{fs}"']
    env.Execute(" ".join(cmd))


env.AddPostAction("$BUILD_DIR/firmware.bin", merge)
# Re-merge after `-t buildfs` too, so the Wokwi image picks up the new filesystem
if env["PIOENV"] == "wokwi":
    env.AddPostAction("$BUILD_DIR/littlefs.bin", merge)
