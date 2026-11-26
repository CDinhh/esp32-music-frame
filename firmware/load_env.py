# Pre-build: turn ../.env into compile-time defines (WIFI_SSID, WIFI_PASS, OTA_PASSWORD) for the real board.
import os

Import("env")

KEYS = ("WIFI_SSID", "WIFI_PASS", "OTA_PASSWORD")


def read_env(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    return out


if env["PIOENV"] == "esp32s3":  # only the real firmware joins WiFi; the sim and wiretest need no secrets
    values = read_env(os.path.join(env.subst("$PROJECT_DIR"), "..", ".env"))
    missing = [k for k in KEYS if not values.get(k)]
    if missing:
        print("*** .env is missing " + ", ".join(missing) + " (copy .env.example to .env at the repo root)")
        env.Exit(1)
    env.Append(CPPDEFINES=[(k, env.StringifyMacro(values[k])) for k in KEYS])
