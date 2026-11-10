#!/usr/bin/env python3
"""Overclock an ESP32-P4 (chip revision v3.0 or later) on ESP-IDF v5.5.4.

  overclock.py status [project]
  overclock.py on --cpu 420 [--psram 260] [project]
  overclock.py off [--unpatch] [project]
  overclock.py calc 455

'on' patches ESP-IDF (once) and selects the clocks in the project's sdkconfig.
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHES = (HERE / "patches" / "cpu.patch", HERE / "patches" / "psram.patch")
IDF_VERSION = (5, 5, 4)
CPU_MHZ = (400, 410, 420, 430, 440, 450, 460, 470)      # 400 is stock
PSRAM_MHZ = (250, 260, 270, 280, 290, 300, 310, 320)    # 250 is stock
XTAL_MHZ = 40
# (div7_0, div_ref) pairs with a history; every other pair is only calculated.
KNOWN = {(10, 0): "stock ESP-IDF",
         (41, 3): "in the patch, booted", (42, 3): "in the patch, booted",
         (43, 3): "in the patch, booted", (11, 0): "in the patch, booted",
         (90, 7): "in the patch, booted",
         (138, 11): "in the patch, measured against the crystal",
         (39, 3): "in the patch, measured against the crystal"}

CPU_LINE = re.compile(r"^(# )?CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ(_\d+)?[ =]")
PSRAM_LINE = re.compile(r"^(# )?CONFIG_SPIRAM_SPEED(_\d+M)?[ =]")


def die(message: str) -> None:
    sys.exit(f"error: {message}")


# --- ESP-IDF patches ---------------------------------------------------------

def idf_path(arg: str | None) -> Path:
    path = arg or os.environ.get("IDF_PATH")
    if not path:
        die("IDF_PATH is not set; run ESP-IDF's export script or pass --idf")
    idf = Path(path)
    header = idf / "components" / "esp_common" / "include" / "esp_idf_version.h"
    if not header.is_file():
        die(f"{idf} is not an ESP-IDF tree")
    text = header.read_text(encoding="utf-8")
    version = tuple(int(re.search(rf"ESP_IDF_VERSION_{part}\s+(\d+)", text).group(1))
                    for part in ("MAJOR", "MINOR", "PATCH"))
    if version != IDF_VERSION:
        die("the patches are for ESP-IDF v%d.%d.%d, this tree is v%d.%d.%d"
            % (IDF_VERSION + version))
    return idf


def git_apply(idf: Path, patch: Path, *flags: str) -> bool:
    return subprocess.run(["git", "apply", *flags, str(patch)], cwd=idf,
                          capture_output=True).returncode == 0


def patch_state(idf: Path, patch: Path) -> str:
    if git_apply(idf, patch, "--reverse", "--check"):
        return "applied"
    if git_apply(idf, patch, "--check"):
        return "not applied"
    return "conflict"


def patch_idf(idf: Path, reverse: bool) -> None:
    want = "not applied" if reverse else "applied"
    for patch in PATCHES:
        state = patch_state(idf, patch)
        if state == "conflict":
            die(f"{patch.name} does not fit {idf}; the tree has other changes to the same files")
        if state != want:
            git_apply(idf, patch, *(["--reverse"] if reverse else []))
            print(f"{patch.name}: {want}")


# --- project configuration ---------------------------------------------------

def config_files(project: Path) -> list[Path]:
    if not (project / "CMakeLists.txt").is_file():
        die(f"{project} is not an ESP-IDF project (no CMakeLists.txt)")
    # sdkconfig wins over the defaults once it exists, so both are kept in step.
    files = [project / "sdkconfig.defaults"]
    if (project / "sdkconfig").is_file():
        files.append(project / "sdkconfig")
    return files


def read_clocks(path: Path) -> tuple[int | None, int | None]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    cpu = re.search(r"^CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_(\d+)=y$", text, re.M)
    psram = re.search(r"^CONFIG_SPIRAM_SPEED_(\d+)M=y$", text, re.M)
    return (int(cpu.group(1)) if cpu else None, int(psram.group(1)) if psram else None)


def write_clocks(project: Path, cpu: int, psram: int | None) -> None:
    files = config_files(project)
    for path in files:
        if path.is_file() and re.search(r"^CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y$",
                                        path.read_text(encoding="utf-8"), re.M):
            die(f"{path.name} targets chip revisions below v3.0; the overclock needs v3.0 or later")
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        lines =[line for line in lines if not CPU_LINE.match(line)
                 and not (psram and PSRAM_LINE.match(line))]
        lines.append(f"CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_{cpu}=y")
        if psram:
            lines.append(f"CONFIG_SPIRAM_SPEED_{psram}M=y")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{path.name}: CPU {cpu} MHz" + (f", PSRAM {psram} MHz" if psram else ""))


# --- CPLL divider calculator -------------------------------------------------

def swap_bits_3_2(value: int) -> int:
    return (value & ~12) | ((value & 4) << 1) | ((value & 8) >> 1)


def cpll_mhz(raw: int, ref: int) -> float:
    """Rev v1.0+ silicon: the multiplier is the raw value with bits 3:2 swapped, plus 4."""
    return XTAL_MHZ * (swap_bits_3_2(raw) + 4) / (ref + 1)


def calc(mhz: int) -> None:
    print(f"CPLL settings for {mhz} MHz (crystal {XTAL_MHZ} MHz):")
    found = False
    for ref in range(16):                         # I2C_CPLL_OC_REF_DIV is 4 bits
        multiplier, rest = divmod(mhz * (ref + 1), XTAL_MHZ)
        if rest or not 4 <= multiplier <= 259:    # I2C_CPLL_OC_DIV_7_0 is 8 bits
            continue
        raw = swap_bits_3_2(multiplier - 4)
        note = KNOWN.get((raw, ref), "calculated, never run")
        print(f"  div7_0 = {raw:3d}, div_ref = {ref:2d}   "
              f"(reference {XTAL_MHZ / (ref + 1):.2f} MHz, x{multiplier})   {note}")
        found = True
    if not found:
        print("  none: no divider pair gives exactly this frequency")
    print("These go in clk_ll_cpll_set_config() in components/hal/esp32p4/include/hal/clk_tree_ll.h.")
    print("Measure the real clock after any change (see test/).")


# --- command line ------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "on", "off", "calc"))
    parser.add_argument("target", nargs="?", help="project folder (default: current), or MHz for calc")
    parser.add_argument("--cpu", type=int, choices=CPU_MHZ, help="CPU clock in MHz, with 'on'")
    parser.add_argument("--psram", type=int, choices=PSRAM_MHZ, help="PSRAM clock in MHz, with 'on'")
    parser.add_argument("--unpatch", action="store_true", help="with 'off': also remove the ESP-IDF patches")
    parser.add_argument("--idf", help="ESP-IDF folder (default: $IDF_PATH)")
    args = parser.parse_intermixed_args()

    if args.action == "calc":
        if not (args.target or "").isdigit():
            parser.error("calc needs a frequency in MHz, for example: calc 455")
        calc(int(args.target))
        return 0

    project = Path(args.target or ".").resolve()
    idf = idf_path(args.idf)
    if args.action == "status":
        for patch in PATCHES:
            print(f"{patch.name}: {patch_state(idf, patch)}")
        for path in config_files(project):
            cpu, psram = read_clocks(path)
            print(f"{path.name}: CPU {cpu or 'not set'}, PSRAM {psram or 'not set'}")
    elif args.action == "on":
        if not args.cpu and not args.psram:
            parser.error("'on' needs --cpu, --psram or both")
        patch_idf(idf, reverse=False)
        cpu = args.cpu or read_clocks(config_files(project)[-1])[0] or CPU_MHZ[0]
        write_clocks(project, cpu, args.psram)
        print("Now build and flash, then run the test in test/ before trusting it.")
    else:
        psram = read_clocks(config_files(project)[-1])[1]
        write_clocks(project, CPU_MHZ[0], PSRAM_MHZ[0] if psram in PSRAM_MHZ else None)
        if args.unpatch:
            patch_idf(idf, reverse=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
