# esp32p4-overclock

I overclocked my ESP32-P4 while working on the ProtoOS kernel, and this is
everything you need to do the same.

**What I run, stable on my board:**

- **CPU: 460 MHz** (stock 400)
- **PSRAM: 320 MHz** (stock 250)

Both go up in 10 MHz steps, so you can find where your own chip tops out.

> You're running the chip past Espressif's rating. Every chip is a little
> different, so yours may land higher or lower than mine. Test before you
> trust it.

## What you need

- An ESP32-P4 with **chip revision v3.0 or newer** (the boot log prints it)
- **ESP-IDF v5.5.4**
- Python 3.8+ and git

## Overclock your project

Open a terminal with ESP-IDF loaded and run:

```sh
python overclock.py on --cpu 420 --psram 260 path/to/your/project
idf.py build flash monitor
```

That's it. `on` patches ESP-IDF once (the extra clock steps show up as normal
menuconfig options) and selects the clocks in your project's sdkconfig.

The rest:

```sh
python overclock.py status  path/to/project   # what's applied and selected
python overclock.py off     path/to/project   # back to 400 / 250
python overclock.py off --unpatch path/to/project   # also restore ESP-IDF
python overclock.py calc 455                  # divider values for any frequency
```

## Find your chip's limit

A clean boot doesn't tell you much. A chip at its limit fails when it's hot
and under load, and it can give wrong results before it crashes. So I
included a check app:

```sh
python overclock.py on --cpu 430 test
idf.py -C test build flash monitor
```

It prints a line like this every 10 seconds:

```
[  120 s] measured CPU 430.004 MHz | psram passes 14 | cpu passes 1310 | ok (0 errors)
```

- **measured CPU** is the real clock, counted against the crystal.
- **psram passes** writes 4 MiB of PSRAM with seven patterns and reads it back.
- **cpu passes** repeats a fixed calculation on the second core and checks the result.

How I'd do it:

1. Start at stock and raise **one** clock by 10 MHz at a time.
2. Let the check run for 20 minutes at each step.
3. A `FAIL`, a reset or a hang means that step is too much. Back off.
4. For daily use, stay a step or two under the first one that failed.

Watch the boot log too: `MSPI Timing: Enter psram timing tuning` has to
finish and the PSRAM memory test has to pass.

## What I found

| Setting | Result |
|---|---|
| **CPU 460 / PSRAM 320** | Stable. This is what I run. |
| CPU 470 | Runs, and the clock is real (I measured 469.96–470.01 MHz), but my app crashed under load. |
| PSRAM 320 | The ceiling. The PLL's divider is 5 bits, so it can't go higher the normal way. |
| PSRAM 340 | I got it running with a different PLL reference. Too much for the chip. |

Code that runs from cache or internal RAM speeds up almost 1:1 with the CPU
clock. Code that misses into PSRAM gains less, and that's what the PSRAM
overclock is for.

## How it works

The P4 has a PLL just for the CPU, fed by the 40 MHz crystal:

```
CPU clock = 40 MHz × multiplier / (div_ref + 1)
```

Stock ESP-IDF only knows the settings for 360 and 400 MHz. My CPU patch adds
the rest. The memory and system clocks stay at half the CPU clock and APB at
a quarter, so they go up with it.

Here's the trap I hit: the multiplier is not the number
you write to the register. It's the register value **with bits 3 and 2
swapped, plus 4**. I wrote 47 expecting 470 MHz and measured 510.
`overclock.py calc` does the encoding for you, and the patch fixes ESP-IDF's
readback so the frequency it reports is the real one.

PSRAM runs off a second PLL at half its frequency. The PSRAM patch adds the
steps and keeps ESP-IDF's timing tuning on for them.

## Other ESP32 chips

This is P4 only. The ESP32-S3, for example, has one PLL for everything: the
CPU, the Wi-Fi and Bluetooth radio, USB and the peripheral clocks. Raise it
and they all move. The P4 has a PLL just for the CPU, which is why this works
so cleanly.

## License

Apache-2.0. The patches modify [ESP-IDF](https://github.com/espressif/esp-idf),
which is Apache-2.0.
