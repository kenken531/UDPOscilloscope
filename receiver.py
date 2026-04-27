"""
UDPOscilloscope 📡 — BUILDCORED ORCAS Day 21
RECEIVER: Listens for UDP sensor packets and renders a live oscilloscope.
Shows waveforms per channel, packet loss %, jitter histogram, sequence gaps.

Usage:
    python receiver.py
    python receiver.py --port 5005
    python receiver.py --window 4.0      # show last 4 seconds
    python receiver.py --no-jitter       # hide jitter panel
"""

import socket
import struct
import time
import threading
import argparse
import collections
import math

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.gridspec as gridspec

# ─── PACKET FORMAT (must match sender exactly) ────────────────────────────────
HEADER_FMT  = ">IQB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
CHANNEL_FMT = ">f"
CHANNEL_SIZE = struct.calcsize(CHANNEL_FMT)
MAGIC       = b"\xCA\xFE"
MAGIC_SIZE  = 2

MAX_CHANNELS = 4

# ─── COLOUR PALETTE ───────────────────────────────────────────────────────────
CH_COLORS = ["#00ffcc", "#ff6b6b", "#ffd93d", "#6bcbff"]
BG_COLOR  = "#0d1117"
GRID_COLOR = "#21262d"
TEXT_COLOR = "#e6edf3"

# ─── SHARED STATE ─────────────────────────────────────────────────────────────
st = {
    "lock":          threading.Lock(),
    "running":       True,

    # Ring buffers: (timestamp_sec, value) per channel
    "ch_times":  [collections.deque(maxlen=5000) for _ in range(MAX_CHANNELS)],
    "ch_values": [collections.deque(maxlen=5000) for _ in range(MAX_CHANNELS)],
    "n_channels": 0,

    # Sequence tracking
    "last_seq":     -1,
    "total_rx":      0,
    "total_expected": 0,
    "gaps":          [],          # list of gap sizes
    "gap_positions": [],          # timestamp of each gap

    # Jitter tracking (inter-packet arrival delta)
    "last_arrival":  None,
    "jitter_buf":    collections.deque(maxlen=500),

    # Stats
    "rx_rate":       0.0,
    "loss_pct":      0.0,
    "recv_start":    None,
}

# ─── UDP RECEIVER THREAD ──────────────────────────────────────────────────────
def udp_thread(host, port, buf_size):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind((host, port))
    sock.settimeout(1.0)

    print(f"[receiver] Listening on {host}:{port} …")

    t_last_rate = time.perf_counter()
    count_since = 0
    recv_start  = None

    while st["running"]:
        try:
            data, _ = sock.recvfrom(buf_size)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[receiver] Socket error: {e}")
            break

        arrival = time.perf_counter()

        # Validate magic bytes
        if len(data) < MAGIC_SIZE or data[:MAGIC_SIZE] != MAGIC:
            continue

        payload = data[MAGIC_SIZE:]
        if len(payload) < HEADER_SIZE:
            continue

        seq, timestamp_us, n_ch = struct.unpack_from(HEADER_FMT, payload, 0)
        n_ch = min(n_ch, MAX_CHANNELS)
        expected_payload = HEADER_SIZE + n_ch * CHANNEL_SIZE
        if len(payload) < expected_payload:
            continue

        # Decode channel values
        values = []
        for i in range(n_ch):
            offset = HEADER_SIZE + i * CHANNEL_SIZE
            (v,) = struct.unpack_from(CHANNEL_FMT, payload, offset)
            values.append(v)

        t_sec = timestamp_us / 1e6

        with st["lock"]:
            if recv_start is None:
                recv_start = arrival
                st["recv_start"] = arrival

            # Jitter
            if st["last_arrival"] is not None:
                delta_ms = (arrival - st["last_arrival"]) * 1000.0
                st["jitter_buf"].append(delta_ms)
            st["last_arrival"] = arrival

            # Sequence gap detection
            if st["last_seq"] >= 0:
                expected_seq = st["last_seq"] + 1
                if seq != expected_seq:
                    gap = seq - expected_seq
                    if gap > 0:
                        st["gaps"].append(gap)
                        st["gap_positions"].append(t_sec)
                        st["total_expected"] += gap
            st["last_seq"] = seq
            st["total_rx"] += 1
            st["total_expected"] += 1

            # Loss %
            if st["total_expected"] > 0:
                st["loss_pct"] = 100.0 * (1 - st["total_rx"] / st["total_expected"])

            # Push data
            st["n_channels"] = max(st["n_channels"], n_ch)
            for i, v in enumerate(values):
                st["ch_times"][i].append(t_sec)
                st["ch_values"][i].append(v)

        count_since += 1
        # Update receive rate every second
        now = time.perf_counter()
        if now - t_last_rate >= 1.0:
            st["rx_rate"] = count_since / (now - t_last_rate)
            count_since = 0
            t_last_rate = now

    sock.close()

# ─── PLOT SETUP ───────────────────────────────────────────────────────────────
def build_figure(window_sec, show_jitter):
    n_rows = MAX_CHANNELS + (1 if show_jitter else 0)
    heights = [3] * MAX_CHANNELS + ([2] if show_jitter else [])
    fig = plt.figure(figsize=(13, 8), facecolor=BG_COLOR)
    gs  = gridspec.GridSpec(n_rows, 1, hspace=0.35, left=0.08, right=0.97,
                            top=0.92, bottom=0.06, height_ratios=heights)

    axes_ch = []
    for i in range(MAX_CHANNELS):
        ax = fig.add_subplot(gs[i])
        ax.set_facecolor(BG_COLOR)
        ax.tick_params(colors=TEXT_COLOR, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(GRID_COLOR)
        ax.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle="--")
        ax.set_xlim(-window_sec, 0)
        ax.set_ylim(-2.2, 2.2)
        ax.set_ylabel(f"CH{i}", color=CH_COLORS[i], fontsize=9, fontweight="bold")
        ax.axhline(0, color=GRID_COLOR, linewidth=0.8)
        if i < MAX_CHANNELS - 1:
            ax.set_xticklabels([])
        axes_ch.append(ax)

    axes_ch[-1].set_xlabel("Time (s, 0 = now)", color=TEXT_COLOR, fontsize=8)

    ax_jitter = None
    if show_jitter:
        ax_jitter = fig.add_subplot(gs[-1])
        ax_jitter.set_facecolor(BG_COLOR)
        ax_jitter.tick_params(colors=TEXT_COLOR, labelsize=8)
        for spine in ax_jitter.spines.values():
            spine.set_color(GRID_COLOR)
        ax_jitter.set_xlabel("Inter-packet gap (ms)", color=TEXT_COLOR, fontsize=8)
        ax_jitter.set_ylabel("Count", color=TEXT_COLOR, fontsize=8)
        ax_jitter.set_title("Jitter histogram", color=TEXT_COLOR, fontsize=9)

    title = fig.suptitle(
        "UDPOscilloscope 📡 — waiting for packets…",
        color=TEXT_COLOR, fontsize=11, fontweight="bold", y=0.97
    )

    return fig, axes_ch, ax_jitter, title

# ─── ANIMATION ────────────────────────────────────────────────────────────────
def make_animate(axes_ch, ax_jitter, title, window_sec):
    lines = []
    gap_vlines = [[] for _ in range(MAX_CHANNELS)]
    for i, ax in enumerate(axes_ch):
        ln, = ax.plot([], [], color=CH_COLORS[i], linewidth=1.2, antialiased=True)
        lines.append(ln)

    def animate(_frame):
        with st["lock"]:
            n_ch     = st["n_channels"]
            loss_pct = st["loss_pct"]
            rx_rate  = st["rx_rate"]
            jitter   = list(st["jitter_buf"])
            gaps_pos = list(st["gap_positions"])
            ch_t     = [list(st["ch_times"][i])  for i in range(MAX_CHANNELS)]
            ch_v     = [list(st["ch_values"][i]) for i in range(MAX_CHANNELS)]

        if n_ch == 0:
            return lines

        # Current time reference (latest packet timestamp)
        t_now = ch_t[0][-1] if ch_t[0] else 0

        # Update title
        title.set_text(
            f"UDPOscilloscope 📡  │  "
            f"Channels: {n_ch}  │  "
            f"Rate: {rx_rate:.0f} pkt/s  │  "
            f"Loss: {loss_pct:.1f}%  │  "
            f"Window: {window_sec:.1f} s"
        )

        for i in range(MAX_CHANNELS):
            ax = axes_ch[i]

            if i < n_ch and ch_t[i]:
                t_arr = np.array(ch_t[i])
                v_arr = np.array(ch_v[i])
                # Convert to relative time (0 = now, negative = past)
                t_rel = t_arr - t_now
                # Window filter
                mask = t_rel >= -window_sec
                lines[i].set_data(t_rel[mask], v_arr[mask])

                # Draw gap markers (vertical red dashes)
                for vl in gap_vlines[i]:
                    try:
                        vl.remove()
                    except Exception:
                        pass
                gap_vlines[i].clear()
                for gpos in gaps_pos:
                    grel = gpos - t_now
                    if -window_sec <= grel <= 0:
                        vl = ax.axvline(grel, color="#ff4444", linewidth=1.0,
                                        linestyle=":", alpha=0.7)
                        gap_vlines[i].append(vl)

                # Dynamic Y range
                vis_v = v_arr[mask]
                if len(vis_v):
                    lo, hi = vis_v.min(), vis_v.max()
                    margin = max(0.2, (hi - lo) * 0.15)
                    ax.set_ylim(lo - margin, hi + margin)

                ax.set_xlim(-window_sec, 0)
                ax.set_visible(True)
            else:
                lines[i].set_data([], [])
                ax.set_visible(i == 0)  # always show at least CH0

        # Jitter histogram
        if ax_jitter is not None and jitter:
            ax_jitter.cla()
            ax_jitter.set_facecolor(BG_COLOR)
            for spine in ax_jitter.spines.values():
                spine.set_color(GRID_COLOR)
            ax_jitter.tick_params(colors=TEXT_COLOR, labelsize=8)
            ax_jitter.set_xlabel("Inter-packet gap (ms)", color=TEXT_COLOR, fontsize=8)
            ax_jitter.set_ylabel("Count", color=TEXT_COLOR, fontsize=8)
            ax_jitter.set_title(
                f"Jitter  │  mean={np.mean(jitter):.2f} ms  │  "
                f"p99={np.percentile(jitter, 99):.2f} ms",
                color=TEXT_COLOR, fontsize=9
            )
            ax_jitter.hist(jitter, bins=40, color="#6bcbff", alpha=0.8,
                           edgecolor=BG_COLOR, linewidth=0.3)
            ax_jitter.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle="--")

        return lines

    return animate

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="UDPOscilloscope Receiver")
    ap.add_argument("--host",      default="127.0.0.1")
    ap.add_argument("--port",      type=int,   default=5005)
    ap.add_argument("--window",    type=float, default=3.0,  help="Display window seconds")
    ap.add_argument("--fps",       type=int,   default=20,   help="Animation FPS")
    ap.add_argument("--buf",       type=int,   default=65536,help="UDP recv buffer size")
    ap.add_argument("--no-jitter", action="store_true",      help="Hide jitter panel")
    args = ap.parse_args()

    # Start UDP thread
    t = threading.Thread(
        target=udp_thread,
        args=(args.host, args.port, args.buf),
        daemon=True,
    )
    t.start()

    # Build figure
    show_jitter = not args.no_jitter
    fig, axes_ch, ax_jitter, title = build_figure(args.window, show_jitter)
    animate_fn = make_animate(axes_ch, ax_jitter, title, args.window)

    ani = animation.FuncAnimation(
        fig,
        animate_fn,
        interval=1000 // args.fps,
        blit=False,
        cache_frame_data=False,
    )

    plt.show(block=True)
    st["running"] = False
    print("\n[receiver] Closed.")

if __name__ == "__main__":
    main()