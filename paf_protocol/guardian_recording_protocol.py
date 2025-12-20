import os
import sys
import csv
import json
import time
import asyncio
import threading
import platform
from datetime import datetime
import tkinter as tk

from dotenv import load_dotenv
load_dotenv()

try:
    import winsound
    HAVE_WINSOUND = True
except Exception:
    HAVE_WINSOUND = False

try:
    from idun_guardian_sdk import GuardianClient
except Exception as e:
    GuardianClient = None
    _sdk_import_error = e


# ---------------- CONFIG ----------------
FS = 250.0
CLOSED_PRE_SEC = 30   # Eyes closed pre-baseline duration
INTERVENTION_SEC = 60  # Intervention/recording duration
CLOSED_POST_SEC = 30   # Eyes closed post-baseline duration
INSTRUCTION_DELAY = 5  # Time between instructions and phase start
OUT_DIR = "recordings"

# Legacy aliases for backward compatibility
OPEN_SEC = CLOSED_PRE_SEC
CLOSE_SEC = INTERVENTION_SEC

TOKEN = (
    os.getenv("IDUN_API_TOKEN")
    or os.getenv("IDUN_GUARDIAN_TOKEN")
    or ""
)


def beep():
    if HAVE_WINSOUND:
        winsound.Beep(1000, 500)
    elif platform.system() == "Darwin":  # macOS
        os.system('afplay /System/Library/Sounds/Glass.aiff')
    else:  # Linux and other Unix-like systems
        print('\a', end='', flush=True)
    print("[INFO] (beep)")


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)


# ---------------- EEG RECORDER ----------------
class EEGRecorder:
    def __init__(self, fs):
        self.fs = fs
        self.samples = []
        self.sample_count = 0
        self.start_time = None

        self.phase_times = {
            "eyes_closed_pre_start": None,
            "eyes_closed_pre_end": None,
            "intervention_start": None,
            "intervention_end": None,
            "eyes_closed_post_start": None,
            "eyes_closed_post_end": None,
        }

        self.phase_indices = {
            "eyes_closed_pre_start_idx": None,
            "eyes_closed_pre_end_idx": None,
            "intervention_start_idx": None,
            "intervention_end_idx": None,
            "eyes_closed_post_start_idx": None,
            "eyes_closed_post_end_idx": None,
        }

    def mark_start(self):
        self.start_time = time.time()

    def rel_sec(self):
        return time.time() - self.start_time if self.start_time else 0.0

    def mark_phase(self, key):
        t = self.rel_sec()
        idx = self.sample_count
        self.phase_times[key] = t
        self.phase_indices[key + "_idx"] = idx
        print(f"[DEBUG] {key}: t={t:.3f}, idx={idx}")

    def add_raw_eeg(self, lst):
        for s in lst:
            try:
                ts = float(s["timestamp"])
                v = float(s["ch1"])
            except Exception:
                continue
            self.samples.append((ts, v))
            self.sample_count += 1

    def snapshot(self):
        return (
            list(self.samples),
            dict(self.phase_times),
            dict(self.phase_indices),
            self.start_time,
        )


# ---------------- GUARDIAN CONTROLLER ----------------
class GuardianController:
    def __init__(self, recorder: EEGRecorder, device_address: str = None):
        self.rec = recorder
        self.device_address = device_address  # Specific device to connect to

        self.loop = None
        self.thread = None
        self.client = None

        self._start_eeg_event = asyncio.Event()
        self._stop_all_event = asyncio.Event()
        self._impedance_stream_active = False
        self._impedance_task = None

        self.last_imp = None
        self.eeg_seen = False
        self.eeg_error = None  # Track any errors starting EEG stream

        # store last 10 impedance samples
        self.last_imp_values = []
        self.MAX_IMP_SAMPLES = 10
        
        # Device information
        self.device_mac_address = None
        self.device_name = None

    def start(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    def trigger_start_eeg(self):
        if self.loop and not self._start_eeg_event.is_set():
            self.loop.call_soon_threadsafe(self._start_eeg_event.set)
    
    def start_impedance_stream(self):
        """Start streaming impedance data (can be called while EEG is running)"""
        if self.loop and self.client and not self._impedance_stream_active:
            print("[IMPEDANCE] Starting impedance stream...")
            self._impedance_stream_active = True
            asyncio.run_coroutine_threadsafe(self._start_impedance_async(), self.loop)
    
    def stop_impedance_stream(self):
        """Stop streaming impedance data"""
        if self._impedance_stream_active and self._impedance_task:
            print("[IMPEDANCE] Stopping impedance stream...")
            self._impedance_stream_active = False
            if self.loop:
                self.loop.call_soon_threadsafe(self._impedance_task.cancel)
    
    async def _start_impedance_async(self):
        """Async method to start impedance streaming"""
        try:
            self._impedance_task = asyncio.create_task(self._run_impedance())
            await self._impedance_task
        except asyncio.CancelledError:
            print("[IMPEDANCE] Impedance stream cancelled")
        except Exception as e:
            print(f"[IMPEDANCE] Error in impedance stream: {e}")
        finally:
            self._impedance_stream_active = False
            self._impedance_task = None

    def stop_all(self):
        """Stop all operations and clean up threads"""
        if self.loop and not self._stop_all_event.is_set():
            try:
                # Signal stop
                self.loop.call_soon_threadsafe(self._stop_all_event.set)
                
                # Wait for thread to finish (with timeout)
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=5)
                    if self.thread.is_alive():
                        print("[WARN] GuardianController thread did not terminate within timeout")
            except Exception as e:
                print(f"[ERROR] Error in stop_all: {e}")

    async def _main(self):
        if GuardianClient is None:
            print("[FATAL] No SDK:", _sdk_import_error)
            return

        # Create client with optional specific device address
        if self.device_address:
            print(f"[INFO] Using specific device address: {self.device_address}")
            if TOKEN:
                self.client = GuardianClient(api_token=TOKEN, address=self.device_address, debug=False)
            else:
                self.client = GuardianClient(address=self.device_address)
        else:
            if TOKEN:
                self.client = GuardianClient(api_token=TOKEN, debug=False)
            else:
                self.client = GuardianClient()
            # Search for device if no address provided
            print("[INFO] Searching for IDUN Guardian device...")
            try:
                await self.client.search_device()
                print("[INFO] Device found")
            except Exception as e:
                print(f"[ERROR] Device search failed: {e}")
                self.eeg_error = str(e)
                return
        
        # Connect to device
        print("[INFO] Connecting to device...")
        try:
            await self.client.connect_device()
            print("[INFO] Device connected")
        except Exception as e:
            print(f"[ERROR] Device connection failed: {e}")
            self.eeg_error = str(e)
            return
        
        # Get device MAC address
        try:
            self.device_mac_address = await self.client.get_device_mac_address()
            print(f"[INFO] Connected device MAC: {self.device_mac_address}")
        except Exception as e:
            print(f"[INFO] Could not get device MAC: {e}")

        print("[INFO] Waiting for EEG start…")

        imp_task = asyncio.create_task(self._run_impedance())

        await self._start_eeg_event.wait()

        print("[INFO] Stopping impedance stream…")
        imp_task.cancel()
        try:
            await imp_task
        except asyncio.CancelledError:
            pass

        eeg_task = asyncio.create_task(self._run_eeg())

        await self._stop_all_event.wait()
        print("[INFO] Stopping EEG stream…")

        try:
            await self.client.stop_recording()
        except Exception:
            pass

        eeg_task.cancel()
        try:
            await eeg_task
        except asyncio.CancelledError:
            pass

        print("[INFO] Controller finished.")

    async def _run_impedance(self):
        try:
            await self.client.stream_impedance(handler=self._imp_handler)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("[ERROR] IMP:", e)

    def _imp_handler(self, data):
        try:
            val = float(data)
            self.last_imp = val

            self.last_imp_values.append(val)
            if len(self.last_imp_values) > self.MAX_IMP_SAMPLES:
                self.last_imp_values.pop(0)
            
            # Log every 10th sample to avoid spam
            if len(self.last_imp_values) % 10 == 0 or len(self.last_imp_values) <= 3:
                print(f"[IMPEDANCE] Received: {val} ohms ({val/1000.0:.1f} kΩ), total samples: {len(self.last_imp_values)}")
        except Exception as e:
            print(f"[IMPEDANCE] Error parsing impedance data '{data}': {e}")

    async def _run_eeg(self):
        try:
            # Try to stop any existing recording first
            try:
                await self.client.stop_recording()
                print("[INFO] Stopped any existing recording")
            except Exception as e:
                # It's okay if there's no recording to stop
                print(f"[INFO] No existing recording to stop (or error): {e}")
            
            self.client.subscribe_live_insights(
                raw_eeg=True,
                filtered_eeg=False,
                handler=self._eeg_handler,
            )
            await self.client.start_recording(recording_timer=3600)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            error_msg = str(e)
            self.eeg_error = error_msg
            print(f"[ERROR] EEG: {error_msg}")
            # If there's still a recording conflict, try to handle it
            if "ongoing" in error_msg.lower() or "recording" in error_msg.lower():
                print("[WARN] Recording conflict detected. Attempting to resolve...")
                try:
                    await self.client.stop_recording()
                    # Wait a bit and try again
                    await asyncio.sleep(1)
                    await self.client.start_recording(recording_timer=3600)
                    print("[INFO] Successfully started recording after resolving conflict")
                    self.eeg_error = None  # Clear error on success
                except Exception as retry_error:
                    retry_error_msg = str(retry_error)
                    self.eeg_error = retry_error_msg
                    print(f"[ERROR] Failed to resolve recording conflict: {retry_error_msg}")

    def _eeg_handler(self, event):
        msg = getattr(event, "message", event)
        eeg = msg.get("raw_eeg", [])
        if eeg:
            self.rec.add_raw_eeg(eeg)
            self.eeg_seen = True


# ---------------- GUI ----------------
class ProtocolGUI:
    def __init__(self, rec, ctrl, out_dir, intervention_label=None):
        self.rec = rec
        self.ctrl = ctrl
        self.out_dir = out_dir

        self.eeg_started = False
        self.ended = False

        # mode: "intervention" or "reliability"
        self.mode = None
        self.intervention_label = intervention_label

        # reliability tracking
        self.reliability_runs_done = 0
        self.reliability_total_runs = 3

        # PAF run state
        self._paf_keys = None
        self._paf_on_done = None

        self.root = tk.Tk()
        self.root.title("IDUN Guardian — PAF Protocol")
        self.root.configure(bg="white")
        self.root.geometry("900x700")

        self.canvas = tk.Canvas(self.root, bg="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.label = tk.Label(
            self.root,
            text="Waiting for Guardian...",
            font=("Helvetica", 24),
            bg="white",
        )
        self.label.place(relx=0.5, rely=0.1, anchor=tk.CENTER)

        self.imp_label = tk.Label(
            self.root,
            text="Impedance: --",
            font=("Helvetica", 18),
            bg="white",
        )
        self.imp_label.place(relx=0.5, rely=0.18, anchor=tk.CENTER)

        # start EEG
        self.start_eeg_btn = tk.Button(
            self.root,
            text="Start EEG Stream",
            font=("Helvetica", 14),
            state=tk.DISABLED,
            command=self.start_eeg,
        )
        self.start_eeg_btn.place(relx=0.3, rely=0.92, anchor=tk.CENTER)

        # PAF mode buttons
        self.start_intervention_btn = tk.Button(
            self.root,
            text="PAF Intervention",
            font=("Helvetica", 14),
            state=tk.DISABLED,
            command=self.start_paf_intervention,
        )
        self.start_intervention_btn.place(relx=0.6, rely=0.92, anchor=tk.CENTER)

        self.start_reliability_btn = tk.Button(
            self.root,
            text="PAF Reliability",
            font=("Helvetica", 14),
            state=tk.DISABLED,
            command=self.start_paf_reliability,
        )
        self.start_reliability_btn.place(relx=0.8, rely=0.92, anchor=tk.CENTER)

        # End stream
        self.end_stream_btn = tk.Button(
            self.root,
            text="Cancel Session",
            font=("Helvetica", 14),
            state=tk.DISABLED,
            command=self.end_stream,
        )
        self.end_stream_btn.place(relx=0.5, rely=0.97, anchor=tk.CENTER)

        # End recording
        self.end_record_btn = tk.Button(
            self.root,
            text="End Recording",
            font=("Helvetica", 14),
            state=tk.DISABLED,
            command=self.end_recording,
        )

        # Next button (for intervention + reliability)
        self.next_btn = tk.Button(
            self.root,
            text="Next",
            font=("Helvetica", 14),
            state=tk.DISABLED,
        )

        self.cross_len = 10
        self.cross_width = 2

        self.root.after(200, self._poll)

    # ----------- POLLING --------------
    def _poll(self):
        imp = self.ctrl.last_imp
        if imp is not None:
            col = "green" if imp < 300000 else "red"
            self.imp_label.config(
                text=f"Impedance: {imp/1000:.1f} kΩ",
                fg=col,
            )
            if not self.eeg_started:
                self.start_eeg_btn.config(state=tk.NORMAL)
                self.label.config(
                    text="Connected — impedance OK.\nStart EEG stream when ready."
                )

        self.root.after(300, self._poll)

    # ----------- start EEG --------------
    def start_eeg(self):
        if self.eeg_started:
            return
        self.eeg_started = True

        self.start_eeg_btn.config(state=tk.DISABLED)
        self.label.config(text="Starting EEG stream…")
        self.imp_label.place_forget()

        self.ctrl.trigger_start_eeg()

        def wait_for_eeg():
            if not self.ctrl.eeg_seen:
                self.root.after(100, wait_for_eeg)
                return
            self._eeg_ready()

        wait_for_eeg()

    def _eeg_ready(self):
        self.label.config(
            text="EEG streaming.\nChoose PAF Intervention or PAF Reliability."
        )
        self.start_intervention_btn.config(state=tk.NORMAL)
        self.start_reliability_btn.config(state=tk.NORMAL)
        self.end_stream_btn.config(state=tk.NORMAL)

    # ----------- Generic PAF sequence --------------
    def _start_paf_sequence(self, run_index: int, on_done):
        # ensure global timer starts once
        if self.rec.start_time is None:
            self.rec.mark_start()

        self._paf_on_done = on_done

        if run_index == 1:
            suffix = ""
        else:
            suffix = f"_{run_index}"

        if suffix == "":
            open_start_key = "eyes_open_start"
            open_end_key = "eyes_open_end"
            closed_start_key = "eyes_closed_start"
            closed_end_key = "eyes_closed_end"
        else:
            open_start_key = f"eyes_open{suffix}_start"
            open_end_key = f"eyes_open{suffix}_end"
            closed_start_key = f"eyes_closed{suffix}_start"
            closed_end_key = f"eyes_closed{suffix}_end"

        self._paf_keys = {
            "open_start": open_start_key,
            "open_end": open_end_key,
            "closed_start": closed_start_key,
            "closed_end": closed_end_key,
        }

        self.label.config(
            text="Open your eyes and fixate on the crosshair.\nWe will begin in 5 seconds."
        )
        self.canvas.delete("all")

        self.root.after(INSTRUCTION_DELAY * 1000, self._phase_open_start)

    def _phase_open_start(self):
        if not self._paf_keys:
            return
        self.label.config(text="")
        self._draw_cross()
        self.rec.mark_phase(self._paf_keys["open_start"])
        self.root.after(int(OPEN_SEC * 1000), self._phase_open_end)

    def _phase_open_end(self):
        if not self._paf_keys:
            return
        self.rec.mark_phase(self._paf_keys["open_end"])
        self.label.config(
            text="Close your eyes.\nA beep will signal when to open.\nBeginning in 5 seconds."
        )
        self.canvas.delete("all")
        self.root.after(INSTRUCTION_DELAY * 1000, self._phase_closed_start)

    def _phase_closed_start(self):
        if not self._paf_keys:
            return
        self.label.config(text="")
        self.canvas.delete("all")
        self.rec.mark_phase(self._paf_keys["closed_start"])
        self.root.after(int(CLOSE_SEC * 1000), self._phase_closed_end)

    def _phase_closed_end(self):
        if not self._paf_keys:
            return
        self.rec.mark_phase(self._paf_keys["closed_end"])
        beep()

        cb = self._paf_on_done
        self._paf_on_done = None
        self._paf_keys = None

        if cb:
            cb()

    # ----------- PAF INTERVENTION MODE --------------
    def start_paf_intervention(self):
        if self.mode is not None:
            return
        self.mode = "intervention"

        self.start_intervention_btn.config(state=tk.DISABLED)
        self.start_reliability_btn.config(state=tk.DISABLED)

        # baseline PAF (run 1)
        self.label.config(
            text="Starting baseline PAF.\nFollow the on-screen instructions."
        )
        self._start_paf_sequence(run_index=1, on_done=self._intervention_after_baseline)

    def _intervention_after_baseline(self):
        self.label.config(
            text="Baseline PAF done.\nDo your intervention now (60 s) plus 20 s buffer."
        )
        self.canvas.delete("all")

        # mark intervention window
        self.rec.mark_phase("intervention_start")
        self.root.after(int(80 * 1000), self._intervention_end_window)

    def _intervention_end_window(self):
        self.rec.mark_phase("intervention_end")
        self.label.config(
            text="Intervention window is over.\nClick 'Start Second PAF' when ready."
        )

        self.next_btn.config(
            text="Start Second PAF",
            state=tk.NORMAL,
            command=self._intervention_start_second_paf,
        )
        self.next_btn.place(relx=0.5, rely=0.9, anchor=tk.CENTER)

    def _intervention_start_second_paf(self):
        self.next_btn.config(state=tk.DISABLED)
        self.next_btn.place_forget()

        self.label.config(
            text="Starting post-intervention PAF.\nFollow the on-screen instructions."
        )
        # second PAF (run 2)
        self._start_paf_sequence(run_index=2, on_done=self._intervention_after_second_paf)

    def _intervention_after_second_paf(self):
        self.label.config(
            text="Post-intervention PAF done.\nClick 'End Recording' to save the file."
        )
        self.end_record_btn.place(relx=0.5, rely=0.9, anchor=tk.CENTER)
        self.end_record_btn.config(state=tk.NORMAL)

    # ----------- PAF RELIABILITY MODE --------------
    def start_paf_reliability(self):
        if self.mode is not None:
            return
        self.mode = "reliability"

        self.start_intervention_btn.config(state=tk.DISABLED)
        self.start_reliability_btn.config(state=tk.DISABLED)

        self.reliability_runs_done = 0
        self._start_reliability_next_run()

    def _start_reliability_next_run(self):
        self.reliability_runs_done += 1
        run_idx = self.reliability_runs_done

        self.label.config(
            text=f"Starting PAF run {run_idx} of {self.reliability_total_runs}.\nFollow the on-screen instructions."
        )
        self._start_paf_sequence(run_index=run_idx, on_done=self._reliability_after_run)

    def _reliability_after_run(self):
        if self.reliability_runs_done < self.reliability_total_runs:
            next_run = self.reliability_runs_done + 1
            self.label.config(
                text=f"PAF run {self.reliability_runs_done} complete.\nClick 'Next PAF' to start run {next_run}."
            )
            self.next_btn.config(
                text="Next PAF",
                state=tk.NORMAL,
                command=self._reliability_start_next,
            )
            self.next_btn.place(relx=0.5, rely=0.9, anchor=tk.CENTER)
        else:
            self.label.config(
                text="PAF reliability runs complete.\nClick 'End Recording' to save the file."
            )
            self.end_record_btn.place(relx=0.5, rely=0.9, anchor=tk.CENTER)
            self.end_record_btn.config(state=tk.NORMAL)

    def _reliability_start_next(self):
        self.next_btn.config(state=tk.DISABLED)
        self.next_btn.place_forget()
        self._start_reliability_next_run()

    # ----------- DRAW CROSS --------------
    def _draw_cross(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        cx, cy = w // 2, h // 2
        self.canvas.create_line(
            cx - self.cross_len,
            cy,
            cx + self.cross_len,
            cy,
            width=self.cross_width,
            fill="black",
        )
        self.canvas.create_line(
            cx,
            cy - self.cross_len,
            cx,
            cy + self.cross_len,
            width=self.cross_width,
            fill="black",
        )

    # ----------- END STREAM (Abort) --------------
    def end_stream(self):
        if self.ended:
            return
        self.ended = True

        self.ctrl.stop_all()

        self.start_eeg_btn.config(state=tk.DISABLED)
        self.start_intervention_btn.config(state=tk.DISABLED)
        self.start_reliability_btn.config(state=tk.DISABLED)
        self.end_stream_btn.config(state=tk.DISABLED)
        self.end_record_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)

        self.canvas.delete("all")
        self.label.config(text="EEG stream ended.\nYou may now close this window.")

        self.end_stream_btn.config(
            text="Close Window",
            state=tk.NORMAL,
            command=self.root.destroy,
        )

    # ----------- SAVE RECORDING --------------
    def end_recording(self):
        if self.ended:
            return
        self.ended = True
        self.end_record_btn.config(state=tk.DISABLED)

        self.ctrl.stop_all()

        samples, times, indices, st = self.rec.snapshot()

        ensure_dir(self.out_dir)
        fname = f"eeg_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        csv_path = os.path.join(self.out_dir, fname + ".csv")
        json_path = os.path.join(self.out_dir, fname + ".json")

        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "ch1"])
            for t, v in samples:
                w.writerow([f"{t:.6f}", f"{v:.6f}"])

        meta = {
            "fs": FS,
            "duration_sec": round(self.rec.rel_sec(), 3),
            "mode": self.mode,
            "intervention_label": self.intervention_label,
            "reliability_runs": int(self.reliability_runs_done),
            "impedance_last_10": self.ctrl.last_imp_values,
            **{
                k + "_sec": (round(v, 3) if v is not None else None)
                for k, v in times.items()
            },
            **indices,
        }

        with open(json_path, "w") as f:
            json.dump(meta, f, indent=2)

        print("[INFO] Saved CSV →", csv_path)
        print("[INFO] Saved JSON →", json_path)

        self.label.config(text="Recording saved.\nYou may now close this window.")
        self.end_record_btn.config(
            text="Close Window",
            state=tk.NORMAL,
            command=self.root.destroy,
        )

    def run(self):
        self.root.mainloop()


# ---------------- MAIN ----------------
def main():
    if GuardianClient is None:
        print("[FATAL] SDK missing:", _sdk_import_error)
        sys.exit(1)

    # free-text intervention label for logging (optional)
    try:
        intervention_label = input(
            "Intervention label (e.g., coffee, jog, water) [optional]: "
        ).strip()
    except EOFError:
        intervention_label = ""
    if not intervention_label:
        intervention_label = None

    rec = EEGRecorder(FS)
    ctrl = GuardianController(rec)
    ctrl.start()

    gui = ProtocolGUI(rec, ctrl, OUT_DIR, intervention_label=intervention_label)
    gui.run()


if __name__ == "__main__":
    main()
