# live_eeg_plot.py
import os
import asyncio
import threading
from collections import deque
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from idun_guardian_sdk import GuardianClient
from threading import Lock
from dotenv import load_dotenv
load_dotenv()


# ---- Config ----
FS = 250.0              # Guardian EEG sample rate (Hz)
WIN_SEC = 10.0          # seconds visible on the plot
PLOT_INTERVAL_MS = 50   # refresh rate of the plot (ms)
USE_FILTERED = True     # prefer filtered EEG if available
Y_LIMIT_UV = 100.0      # initial y-axis limit in microvolts

# ---- Buffers ----
buf = deque(maxlen=int(FS * WIN_SEC))     # stores (timestamp, ch1)
buf_lock = Lock()

def on_live_event(event):
    """IDUN live-insights callback: receives ~20 samples per chunk."""
    msg = event.message
    eeg = None
    if USE_FILTERED:
        eeg = msg.get("filtered_eeg")
    if eeg is None:
        eeg = msg.get("raw_eeg", [])
    if not eeg:
        return
    with buf_lock:
        for s in eeg:
            # Each s has: {"timestamp": <unix float>, "ch1": <microvolts>}
            buf.append((s["timestamp"], float(s["ch1"])))


async def stream_task():
    """Connects to the Guardian and starts live streaming."""
    import sys
    print("stream_task() called", flush=True)
    
    token = os.getenv("IDUN_API_TOKEN")
    if not token:
        print("ERROR: IDUN_API_TOKEN not set", flush=True)
        raise SystemExit("IDUN_API_TOKEN not set. In PowerShell: $env:IDUN_API_TOKEN='...'")
    
    print(f"Token found: {token[:10]}...", flush=True)
    print("Creating GuardianClient...", flush=True)
    client = GuardianClient(api_token=token, debug=False)
    print("GuardianClient created", flush=True)

    # Search for device
    print("Searching for IDUN Guardian device...", flush=True)
    try:
        await client.search_device()
        print("Device found", flush=True)
    except Exception as e:
        print(f"ERROR in search_device: {e}", flush=True)
        raise

    # Connect to device (REQUIRED before subscribing)
    print("Connecting to device...", flush=True)
    try:
        await client.connect_device()
        print("connect_device() completed", flush=True)
    except Exception as e:
        print(f"ERROR in connect_device: {e}", flush=True)
        raise
    
    # Check MAC address and reject F3 device
    REJECTED_DEVICE_MAC = "F3-27-99-5E-1A-44"
    try:
        actual_mac = await client.get_device_mac_address()
        print(f"Connected device MAC: {actual_mac}", flush=True)
        if actual_mac and REJECTED_DEVICE_MAC.lower() in actual_mac.lower():
            print(f"ERROR: Connected to rejected device {REJECTED_DEVICE_MAC}", flush=True)
            await client.disconnect_device()
            raise SystemExit(f"Rejected device: {REJECTED_DEVICE_MAC}")
    except AttributeError:
        print("get_device_mac_address not available", flush=True)
    except Exception as e:
        print(f"Error getting MAC address: {e}", flush=True)
    
    print("Device connected successfully!", flush=True)

    # Subscribe to live EEG (handler is called for each chunk)
    print("Subscribing to live insights...", flush=True)
    try:
        client.subscribe_live_insights(raw_eeg=True, filtered_eeg=True, handler=on_live_event)
        print("Live insights subscription active", flush=True)
    except Exception as e:
        print(f"ERROR subscribing to live insights: {e}", flush=True)
        raise

    # Start recording to drive the live stream (set a long timer; Ctrl+C to quit)
    # You can also stop via client.stop_recording() if you wire a signal handler.
    print("Starting recording...", flush=True)
    try:
        await client.start_recording(recording_timer=60*60)  # 1 hour
        print("Recording started - data should start flowing", flush=True)
    except Exception as e:
        print(f"ERROR starting recording: {e}", flush=True)
        raise

def start_async_stream_in_background():
    """Run the asyncio streaming task in a background thread."""
    def runner():
        import sys
        print("=" * 60, flush=True)
        print("BACKGROUND THREAD STARTED", flush=True)
        print("=" * 60, flush=True)
        try:
            print("Calling stream_task()...", flush=True)
            asyncio.run(stream_task())
            print("stream_task() completed", flush=True)
        except SystemExit as e:
            print(f"SystemExit in stream_task: {e}", flush=True)
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"ERROR in stream_task: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            print("Background thread finished", flush=True)

    try:
        t = threading.Thread(target=runner, daemon=True)
        t.start()
        return t
    except Exception as e:
        print(f"ERROR starting background thread: {e}", flush=True)
        raise

# ---- Plot setup ----
fig, ax = plt.subplots(figsize=(10, 4))
line, = ax.plot([], [], lw=1.2)
ax.set_title("IDUN Guardian — Live EEG (ch1)")
ax.set_xlabel("Time (s, relative)")
ax.set_ylabel("Amplitude (µV)")
ax.set_xlim(-WIN_SEC, 0.0)
ax.set_ylim(-Y_LIMIT_UV, Y_LIMIT_UV)
ax.grid(True, alpha=0.3)

def init_plot():
    line.set_data([], [])
    return line,

def update_plot(_frame):
    # Build time axis relative to last timestamp (right edge = 0 s)
    with buf_lock:
        if not buf:
            return line,
        ts, uv = zip(*buf)         # tuples to lists
        ts = np.asarray(ts, dtype=float)
        uv = np.asarray(uv, dtype=float)

    t_last = ts[-1]
    t_rel = ts - t_last           # now the newest sample is at 0.0 s
    # Keep exactly the visible window (should already be enforced by deque)
    # but this guards against weird timing if FS changes.
    mask = (t_rel >= -WIN_SEC)
    t_rel = t_rel[mask]
    uv = uv[mask]

    # Optional: gentle autoscale if the signal exceeds current limits
    ymin, ymax = ax.get_ylim()
    m = np.max(np.abs(uv)) if uv.size else 0.0
    if m > 0.8 * max(abs(ymin), abs(ymax)):
        new_lim = max(50.0, np.ceil(m / 25.0) * 25.0)  # step to nearest 25 µV
        ax.set_ylim(-new_lim, new_lim)

    line.set_data(t_rel, uv)
    return line,

def main():
    # Start streaming in the background
    start_async_stream_in_background()

    # Launch animated plot
    ani = FuncAnimation(fig, update_plot, init_func=init_plot,
                        interval=PLOT_INTERVAL_MS, blit=True)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
