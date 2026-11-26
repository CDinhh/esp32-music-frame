# Writes firmware-merged.bin (bootloader + partitions + app) for one-shot flashing at 0x0 with esptool-js.
Import("env")


def merge(source, target, env):
    build = env.subst("$BUILD_DIR")
    images = [env.subst(str(x)) for x in env.Flatten(env.get("FLASH_EXTRA_IMAGES", []))]
    esptool = env.PioPlatform().get_package_dir("tool-esptool") + "/esptool"  # .exe on Windows
    cmd = [
        f'"{esptool}"', "--chip", "esp32s3", "merge-bin",
        "-o", f'"{build}/firmware-merged.bin"', "--flash-mode", "keep", "--flash-size", "16MB",
    ] + [f'"{x}"' for x in images] + ["$ESP32_APP_OFFSET", f'"{build}/firmware.bin"']
    env.Execute(" ".join(cmd))


env.AddPostAction("$BUILD_DIR/firmware.bin", merge)
