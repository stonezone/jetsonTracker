# Emit a flashable .uf2 after each build. The base PlatformIO nRF52 builder
# produces .hex + an OTA .zip (which we must NOT use — OTA bricks this board);
# the drag-drop bootloader needs .uf2. This closes that gap so `pio run`
# actually yields the artifact the README tells you to flash.
import os
Import("env")  # noqa: F821

def make_uf2(source, target, env):
    import subprocess
    build_dir = env.subst("$BUILD_DIR")
    progname = env.subst("$PROGNAME")
    hexfile = os.path.join(build_dir, progname + ".hex")
    uf2file = os.path.join(build_dir, progname + ".uf2")
    pkg = env.PioPlatform().get_package_dir("framework-arduinoadafruitnrf52")
    uf2conv = os.path.join(pkg, "tools", "uf2conv", "uf2conv.py")
    # 0xADA52840 = Adafruit nRF52840 family ID; app start is 0x26000 (from hex).
    subprocess.run(["python3", uf2conv, "-c", "-f", "0xADA52840",
                    "-o", uf2file, hexfile], check=True)
    print("UF2 ready: " + uf2file)

env.AddPostAction("$BUILD_DIR/${PROGNAME}.hex", make_uf2)  # noqa: F821
