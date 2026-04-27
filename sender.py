"""
UDPOscilloscope 📡 — BUILDCORED ORCAS Day 21
SENDER: Transmits simulated multi-channel sensor data over localhost UDP.
Supports sine waves + noise, configurable packet loss, sequence numbers.

Usage:
    python sender.py
    python sender.py --loss 0.15        # 15% packet loss
    python sender.py --channels 3       # 3 sine channels
    python sender.py --rate 500         # 500 packets/sec
    python sender.py --port 5005
"""

import socket
import struct
import time
import math
import random
import argparse
import sys
import threading

# ─── PACKET FORMAT ─────────────────────────────────────────────────────────────
# Header: seq_num (uint32), timestamp_us (uint64), channel_count (uint8)
# Per channel: value (float32)
# Total for N channels: 4 + 8 + 1 + N*4 bytes
HEADER_FMT  = ">IQB"          # big-endian: uint32, uint64, uint8
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # = 13
CHANNEL_FMT = ">f"            # big-endian float32
CHANNEL_SIZE = struct.calcsize(CHANNEL_FMT) # = 4

MAGIC = b"\xCA\xFE"           # 2-byte frame start marker

# ─── CHANNEL DEFINITIONS ──────────────────────────────────────────────────────
# Each channel: (frequency_hz, amplitude, phase_offset_rad, noise_sigma)
CHANNEL_DEFS = [
    (2.0,  1.0,  0.0,          0.03),   # ch0: 2 Hz sine, low noise
    (5.0,  0.6,  math.pi/3,    0.06),   # ch1: 5 Hz sine, medium noise
    (0.5,  1.4,  math.pi,      0.02),   # ch2: slow 0.5 Hz sine, very clean
    (10.0, 0.4,  math.pi/6,    0.10),   # ch3: fast 10 Hz, noisy
]

def build_packet(seq: int, timestamp_us: int, values: list[float]) -> bytes:
    n = len(values)
    header = struct.pack(HEADER_FMT, seq, timestamp_us, n)
    payload = b"".join(struct.pack(CHANNEL_FMT, v) for v in values)
    return MAGIC + header + payload

def main():
    ap = argparse.ArgumentParser(description="UDPOscilloscope Sender")
    ap.add_argument("--host",     default="127.0.0.1")
    ap.add_argument("--port",     type=int,   default=5005)
    ap.add_argument("--rate",     type=int,   default=200,  help="Packets per second")
    ap.add_argument("--loss",     type=float, default=0.0,  help="Packet loss fraction 0.0–1.0")
    ap.add_argument("--channels", type=int,   default=2,    help="Number of channels (1–4)")
    args = ap.parse_args()

    n_ch     = max(1, min(4, args.channels))
    interval = 1.0 / args.rate
    ch_defs  = CHANNEL_DEFS[:n_ch]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)

    print(f"[sender] Transmitting {n_ch} channel(s) → {args.host}:{args.port}")
    print(f"[sender] Rate: {args.rate} pkt/s | Loss: {args.loss*100:.1f}%")
    print(f"[sender] Packet size: {HEADER_SIZE + n_ch * CHANNEL_SIZE + 2} bytes")
    print(f"[sender] Channels: {[f'{d[0]} Hz' for d in ch_defs]}")
    print(f"[sender] Ctrl+C to stop\n")

    seq      = 0
    sent     = 0
    dropped  = 0
    t_start  = time.perf_counter()

    # Status line thread
    status_stop = threading.Event()
    def status_loop():
        while not status_stop.is_set():
            elapsed = time.perf_counter() - t_start
            actual_rate = sent / elapsed if elapsed > 0 else 0
            loss_pct = 100.0 * dropped / seq if seq > 0 else 0
            print(
                f"\r[sender] seq={seq:>7}  sent={sent:>7}  dropped={dropped:>5}"
                f"  loss={loss_pct:4.1f}%  rate={actual_rate:5.1f} pkt/s",
                end="", flush=True
            )
            time.sleep(0.5)
    st = threading.Thread(target=status_loop, daemon=True)
    st.start()

    try:
        while True:
            t0 = time.perf_counter()
            now_us = int((t0 - t_start) * 1e6)

            # Generate channel values
            t_sec = t0 - t_start
            values = []
            for freq, amp, phase, noise in ch_defs:
                v = amp * math.sin(2 * math.pi * freq * t_sec + phase)
                v += random.gauss(0, noise)
                values.append(v)

            pkt = build_packet(seq, now_us, values)
            seq += 1

            # Simulate packet loss
            if random.random() >= args.loss:
                sock.sendto(pkt, addr)
                sent += 1
            else:
                dropped += 1

            # Precise timing
            elapsed = time.perf_counter() - t0
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    except KeyboardInterrupt:
        status_stop.set()
        total = time.perf_counter() - t_start
        print(f"\n\n[sender] Stopped. {sent} packets sent in {total:.1f}s "
              f"({sent/total:.1f} pkt/s actual). {dropped} dropped.")
        sock.close()

if __name__ == "__main__":
    main()