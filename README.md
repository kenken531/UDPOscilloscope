# UDPOscilloscope 📡

UDPOscilloscope is a **two-process UDP streaming oscilloscope**. A sender process generates simulated multi-channel sensor data (sine waves + Gaussian noise) and transmits framed packets over localhost UDP at a configurable rate. A separate receiver process listens, decodes the packets, and renders a live oscilloscope with per-channel waveforms, a jitter histogram, and real-time packet loss tracking. Introduce controlled loss on the sender and watch the waveform degrade with red gap markers. It's built for the **BUILDCORED ORCAS — Day 21** challenge.

## How it works

### Sender (`sender.py`)
- Generates up to 4 sine-wave channels, each with its own frequency, amplitude, phase, and noise level (e.g. 2 Hz clean, 5 Hz medium-noise, 0.5 Hz slow, 10 Hz fast+noisy).
- Each packet is a framed binary structure: `[MAGIC 0xCAFE] [seq uint32] [timestamp_us uint64] [n_channels uint8] [float32 × n_channels]`.
- **Magic bytes** (`0xCAFE`) let the receiver detect framing errors and discard corrupt or misaligned packets.
- **Sequence numbers** allow the receiver to detect gaps (dropped packets) without any acknowledgement round-trip — the same technique used in RTP, UART streams, and CAN bus.
- `--loss` fraction randomly drops packets before `sendto()`, simulating real network or serial link loss.
- A status line prints live: seq count, packets sent, packets dropped, achieved rate.

### Receiver (`receiver.py`)
- A background UDP thread binds the socket, validates the magic header, unpacks each packet with `struct.unpack`, and pushes (timestamp, value) pairs into per-channel ring deques (5000 points each).
- Gap detection: if `seq != last_seq + 1`, the difference is a gap — counted, time-stamped, and visualised as a red dotted vertical line on the waveform.
- Jitter tracking: inter-arrival delta (ms) between consecutive packets is stored in a rolling 500-sample deque and displayed as a histogram with mean and p99 labels.
- `matplotlib.animation.FuncAnimation` redraws at 20 FPS. All channel axes share a sliding time window (default 3 s, 0 = now, negative = past). Y-axis auto-scales to the visible data.
- Packet loss % is computed from `(total_expected - total_rx) / total_expected` using sequence number accounting — not timing, so it's exact even with jitter.

### Packet framing diagram

```
Byte:  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14 …
       ├────┤────┤────────────────────┤────────────────────────┤────┤────────…
       MAGIC  │    SEQ (uint32 BE)    │  TIMESTAMP_US (uint64) │ N  │ CH0 f32…
       0xCAFE │                      │                         │    │
```

## Requirements

- Python 3.10.x
- tkinter (bundled with Python on Windows and most Linux distros)

## Python packages

```bash
pip install numpy matplotlib
```

Standard library only beyond that: `socket`, `struct`, `threading`, `collections`, `argparse`.

Or:

```bash
pip install -r requirements.txt
```

## Setup

1. Install packages above.
2. Open **two terminal windows** in the same directory.

## Usage

**Terminal 1 — start the receiver first:**
```bash
python receiver.py
```

**Terminal 2 — start the sender:**
```bash
python sender.py
```

The oscilloscope window opens immediately and begins drawing waveforms as packets arrive.

### Introduce packet loss

```bash
python sender.py --loss 0.10    # 10% loss — slight dropouts
python sender.py --loss 0.30    # 30% loss — clearly degraded waveform
python sender.py --loss 0.60    # 60% loss — heavy gaps, broken sine
```

Red dotted vertical lines on the waveform mark the exact position of each detected gap.

### Sender options

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Destination IP |
| `--port` | `5005` | UDP port |
| `--rate` | `200` | Packets per second |
| `--loss` | `0.0` | Packet loss fraction (0.0–1.0) |
| `--channels` | `2` | Number of sine channels (1–4) |

### Receiver options

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind IP |
| `--port` | `5005` | UDP port |
| `--window` | `3.0` | Oscilloscope time window (seconds) |
| `--fps` | `20` | Animation frame rate |
| `--no-jitter` | off | Hide the jitter histogram panel |

### All 4 channels, 500 pkt/s, 20% loss

```bash
# Terminal 1
python receiver.py --window 5.0

# Terminal 2
python sender.py --channels 4 --rate 500 --loss 0.20
```

## Common fixes

**Receiver sees nothing** — sender and receiver must use the same `--port`. Default is 5005 on both. Confirm with `python sender.py --port 5005` and `python receiver.py --port 5005`.

**Waveform is flat / garbled** — `struct` pack/unpack format mismatch. Both files use `">IQB"` for the header and `">f"` per channel. If you modify one, update both identically. The magic byte check (`0xCAFE`) will silently discard misaligned packets.

**Firewall blocks localhost UDP** — rare but possible on hardened systems. On Windows: allow Python through Windows Defender Firewall, or try `--host 0.0.0.0` on the receiver. On Linux: `sudo ufw allow 5005/udp`.

**Port 5005 already in use** — use `--port 5006` (or any free port) on both sender and receiver.

**Both processes are needed** — sender generates and transmits; receiver listens and renders. Running only one does nothing visible. Always start the receiver first so it's ready when packets arrive.

**tkinter error on Linux** — `sudo apt install python3-tk`.

**Animation lags at high rate** — reduce sender `--rate` or increase receiver `--fps`. The ring buffer holds 5000 points per channel; at 500 pkt/s that's 10 seconds before overwrite. Reduce `--window` if the display feels sluggish.

**Jitter histogram is empty** — you need at least a few seconds of data before the histogram fills up. Wait 5–10 seconds after connecting.

## Hardware concept

This project mirrors real **embedded sensor streaming** challenges:

| This project | Real hardware equivalent |
|---|---|
| UDP socket | UART serial port, CAN bus, Ethernet |
| `struct.pack(">IQB")` | Fixed binary frame format (e.g. 0xAA header + length byte) |
| Magic bytes `0xCAFE` | Start-of-frame (SOF) byte in UART framing |
| Sequence numbers | RTP sequence, CAN message ID, UART packet counter |
| `--loss` simulation | Bit errors, EMI, cable disconnects, buffer overflows |
| Jitter histogram | Oscilloscope trigger jitter measurement |
| Ring buffer deque | Hardware FIFO / circular DMA buffer |

Every real sensor that streams data over a wire — MPU6050 over I²C, GPS over UART, lidar over UDP — faces the same framing, ordering, and loss challenges implemented here.

## v2.0 bridge

In v2.0, replace `sender.py` with a Raspberry Pi Pico reading a real sensor over UART and forwarding over USB-serial or Wi-Fi UDP. The receiver code stays identical — swap the socket for a `serial.Serial` read and the same `struct.unpack` decoding applies.

```python
# Pico side (MicroPython) — same packet format
import struct, time
from machine import UART

uart = UART(0, baudrate=115200)
seq = 0
while True:
    t_us = time.ticks_us()
    v = read_sensor()    # your ADC / I2C call
    pkt = b"\xCA\xFE" + struct.pack(">IQBf", seq, t_us, 1, v)
    uart.write(pkt)
    seq += 1
    time.sleep_ms(5)     # 200 Hz
```

## Credits

- Numerical computing: [NumPy](https://numpy.org/)
- Visualization: [Matplotlib](https://matplotlib.org/)

Built as part of the **BUILDCORED ORCAS — Day 21: UDPOscilloscope** challenge.
