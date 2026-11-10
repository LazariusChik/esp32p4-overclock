# esp32p4-overclock

Run an ESP32-P4 past its rated clocks: CPU up to 470 MHz (stock 400) and
PSRAM up to 320 MHz (stock 250), in 10 MHz steps.

> **This runs the chip past Espressif's rating.** It was tried on one board.
> Your chip may top out lower, and a clock that is too high can corrupt data
> silently before anything crashes. Don't ship it in a product.

## What you need

- An ESP32-P4 with **chip revision v3.0 or later** (the boot log prints the
  revision). Older revisions are rated 360 MHz and are not covered.
- **ESP-IDF v5.5.4**, as a git checkout. The tool refuses other versions.
- For the PSRAM steps: the 16-line (HEX) PSRAM that P4 modules ship with.
- Python 3.8+ and git.

## Use it

Open a terminal with the ESP-IDF environment loaded, then:

```sh
python overclock.py on --cpu 420 --psram 260 path/to/your/project
idf.py build flash monitor
```

`on` does two things:

1. Applies the two patches in `patches/` to your ESP-IDF tree, once. They add
   the extra clock steps as normal menuconfig options, and change nothing
   until a step is selected.
2. Selects the clocks in the project's `sdkconfig.defaults`, and in
   `sdkconfig` if it exists.

Other commands:

```sh
python overclock.py status  path/to/project   # patches applied? clocks selected?
python overclock.py off     path/to/project   # back to 400 / 250
python overclock.py off --unpatch path/to/project   # also restore ESP-IDF
python overclock.py calc 455                  # CPLL divider values for any frequency
```

## Find your chip's limit

A clean boot proves little. A chip at its limit fails hot and under load.

1. Start at stock and raise **one** clock by 10 MHz at a time.
2. Flash the check in `test/` at each step:

   ```sh
   python overclock.py on --cpu 430 test
   idf.py -C test build flash monitor
   ```

   It prints a line like this every 10 seconds:

   ```
   [  120 s] measured CPU 430.004 MHz | psram passes 14 | cpu passes 1310 | ok (0 errors)
   ```

   - **measured CPU** is counted against the crystal. It must match the step
     you selected.
   - **psram passes** writes and reads back 4 MiB of PSRAM with seven patterns.
   - **cpu passes** repeats a fixed calculation on the second core and
     compares the result.
3. Let it run for 20 minutes or more. A `FAIL` line, a reset or a hang means
   the step is too high.
4. For daily use, stay two steps below the first step that failed.

During boot, `MSPI Timing: Enter psram timing tuning` must finish and the
PSRAM memory test must pass. That is the earliest warning for the PSRAM clock.

## What was found on one board

Waveshare ESP32-P4 board, chip revision v3, HEX PSRAM:

| Setting | Result |
|---|---|
| CPU 460, PSRAM 320 | The author's daily setting. No long soak test was recorded. |
| CPU 470 | The clock is real (measured 469.96–470.01 MHz), but an app crashed under load. |
| PSRAM 320 | The highest step stock ESP-IDF can encode: the MPLL divider is 5 bits, so the MPLL stops at 640 MHz. |
| PSRAM 340 | Reachable with a different MPLL reference, but too much for the chip. Not included. |

Code that runs from cache or internal RAM speeds up almost in proportion to
the CPU clock. Code that misses into PSRAM gains less, which is what the
PSRAM steps are for. Flash, display and timer clocks do not change.

## How it works

The P4 has a PLL just for the CPU (the CPLL), fed by the 40 MHz crystal:

```
CPU clock = 40 MHz × multiplier / (div_ref + 1)
```

Stock ESP-IDF only knows the settings for 360 and 400 MHz. The CPU patch adds
more. The memory and system clocks stay at half the CPU clock and APB at a
quarter, so they rise with it.

One trap: on chip revision v1.0 and later, the multiplier is not the register
value. It is the register value **with bits 3 and 2 swapped, plus 4**.
Writing 47 to get 470 MHz gave a measured 510 MHz. `overclock.py calc` does
this encoding for you. The patch also fixes ESP-IDF's clock readback to
decode the same way, so the reported frequency is the real one.

PSRAM is clocked from a second PLL (the MPLL) at half its frequency. The PSRAM
patch adds the steps and keeps ESP-IDF's timing tuning on for them.

**Other ESP32 chips are not supported.** The ones with a radio take the CPU
clock from a fixed PLL that the radio also needs, so they have no equivalent
divider to raise.

## License

Apache-2.0. The patches modify [ESP-IDF](https://github.com/espressif/esp-idf),
which is Apache-2.0.
