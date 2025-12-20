"""
PAF Protocol Service - Backend integration of PAF protocol components
Extracts protocol logic from ProtocolGUI for use in FastAPI backend
"""

import os
import sys
import json
import time
import asyncio
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from paf_protocol.guardian_recording_protocol import (
        EEGRecorder,
        GuardianController,
        FS,
        CLOSED_PRE_SEC,
        INTERVENTION_SEC,
        CLOSED_POST_SEC,
        INSTRUCTION_DELAY,
        ensure_dir,
        beep,
    )
except ImportError:
    from ..paf_protocol.guardian_recording_protocol import (
        EEGRecorder,
        GuardianController,
        FS,
        CLOSED_PRE_SEC,
        INTERVENTION_SEC,
        CLOSED_POST_SEC,
        INSTRUCTION_DELAY,
        ensure_dir,
        beep,
    )

try:
    from idun_guardian_sdk import GuardianClient
except Exception as e:
    GuardianClient = None
    _sdk_import_error = e


class ProtocolPhase(Enum):
    """Protocol phase enumeration"""
    IDLE = "idle"
    IMPEDANCE_CHECK = "impedance_check"
    # New protocol flow: eyes closed -> intervention -> eyes closed
    INSTRUCTIONS_PRE = "instructions_pre"      # Instructions before pre-baseline
    EYES_CLOSED_PRE = "eyes_closed_pre"        # 30s eyes closed pre-baseline
    INTERVENTION = "intervention"               # 60s intervention/recording
    INSTRUCTIONS_POST = "instructions_post"    # Instructions before post-baseline
    EYES_CLOSED_POST = "eyes_closed_post"      # 30s eyes closed post-baseline
    COMPLETE = "complete"
    ERROR = "error"


class PAFProtocolService:
    """
    Service that manages PAF protocol execution in the backend.
    Can be controlled via API endpoints and emits events via callbacks.
    """
    
    def __init__(self, out_dir: str = "recordings"):
        self.out_dir = out_dir
        self.recorder = EEGRecorder(FS)  # Single recorder for entire session
        self.controller: Optional[GuardianController] = None
        
        self.current_phase = ProtocolPhase.IDLE
        self.phase_callbacks = []  # List of callback functions for phase changes
        self.protocol_started = False
        self.protocol_complete = False
        
        # Protocol timing
        self.phase_start_time = None
        self.phase_timer: Optional[threading.Timer] = None
        
        # Intervention support
        self.intervention_label: Optional[str] = None
        self.impedance_check_duration = 10.0  # seconds
        self.impedance_samples = []  # Store impedance during check
        self.baseline_complete = False
        
    def add_phase_callback(self, callback):
        """Add a callback function that will be called on phase changes.
        Callback signature: callback(phase: ProtocolPhase, message: str, remaining_sec: Optional[int])
        """
        self.phase_callbacks.append(callback)
    
    def _notify_phase_change(self, phase: ProtocolPhase, message: str, remaining_sec: Optional[int] = None):
        """Notify all callbacks of a phase change"""
        print(f"[PROTOCOL] _notify_phase_change: {phase.value}, callbacks: {len(self.phase_callbacks)}")
        self.current_phase = phase
        for callback in self.phase_callbacks:
            try:
                callback(phase, message, remaining_sec)
                print(f"[PROTOCOL] Callback executed successfully")
            except Exception as e:
                print(f"[ERROR] Phase callback failed: {e}")
                import traceback
                traceback.print_exc()
    
    def initialize_guardian(self, token: Optional[str] = None, device_address: Optional[str] = None):
        """Initialize the Guardian controller with optional specific device address"""
        if self.controller is not None:
            return True
            
        if GuardianClient is None:
            print(f"[ERROR] Guardian SDK not available: {_sdk_import_error}")
            return False
        
        # Controller uses the recorder instance (like GUI does)
        self.controller = GuardianController(self.recorder, device_address=device_address)
        self.controller.start()
        return True
    
    def get_impedance(self) -> Optional[float]:
        """Get current impedance value"""
        if self.controller:
            return self.controller.last_imp
        return None
    
    def get_impedance_stats(self, verbose: bool = False) -> Dict[str, Any]:
        """Get impedance statistics from the check period"""
        if not self.impedance_samples:
            if verbose:
                print("[IMPEDANCE] No samples collected, returning empty stats")
            return {
                "count": 0,
                "mean_kohm": None,
                "min_kohm": None,
                "max_kohm": None,
                "quality": "unknown"
            }
        
        mean_imp = sum(self.impedance_samples) / len(self.impedance_samples)
        min_imp = min(self.impedance_samples)
        max_imp = max(self.impedance_samples)
        
        # Quality: good if mean < 300 kOhm (300,000 ohms)
        quality = "good" if mean_imp < 300000 else "poor"
        mean_kohm = mean_imp / 1000.0
        
        if verbose:
            print(f"[IMPEDANCE] Stats: count={len(self.impedance_samples)}, mean={mean_kohm:.1f} kΩ, quality={quality}")
        
        return {
            "count": len(self.impedance_samples),
            "mean_kohm": mean_kohm,
            "min_kohm": min_imp / 1000.0,
            "max_kohm": max_imp / 1000.0,
            "quality": quality
        }
    
    def get_device_info(self) -> Dict[str, Any]:
        """Get connected device information (MAC address, name, etc.)"""
        if not self.controller:
            return {
                "mac_address": None,
                "name": None
            }
        
        return {
            "mac_address": getattr(self.controller, 'device_mac_address', None),
            "name": getattr(self.controller, 'device_name', None)
        }
    
    def start_eeg_stream(self):
        """Start the EEG stream"""
        if not self.controller:
            return False
        self.controller.trigger_start_eeg()
        return True
    
    def is_eeg_ready(self) -> bool:
        """Check if EEG stream is ready"""
        if not self.controller:
            return False
        return self.controller.eeg_seen
    
    def get_eeg_error(self) -> Optional[str]:
        """Get any error that occurred while starting EEG stream"""
        if not self.controller:
            return None
        return getattr(self.controller, 'eeg_error', None)
    
    def start_impedance_check(self):
        """Start impedance check phase (10 seconds).
        
        NOTE: This must be called BEFORE starting EEG stream.
        The impedance stream is automatically started when the device connects
        and is stopped when EEG recording starts.
        """
        if self.protocol_started:
            return False
        
        print(f"[IMPEDANCE] Starting impedance check (duration: {self.impedance_check_duration}s)")
        print(f"[IMPEDANCE] Note: Impedance streaming should already be active from device connection")
        self.impedance_samples = []
        
        self._notify_phase_change(
            ProtocolPhase.IMPEDANCE_CHECK,
            "Checking impedance. Keep device still...",
            int(self.impedance_check_duration)
        )
        
        # Collect impedance samples from the already-running stream
        def collect_impedance():
            start_time = time.time()
            sample_count = 0
            print(f"[IMPEDANCE] Collection thread started")
            while time.time() - start_time < self.impedance_check_duration:
                imp = self.get_impedance()
                if imp is not None:
                    self.impedance_samples.append(imp)
                    sample_count += 1
                    if sample_count <= 3 or sample_count % 5 == 0:
                        print(f"[IMPEDANCE] Sample #{sample_count}: {imp} ohms ({imp/1000.0:.1f} kΩ)")
                else:
                    if sample_count == 0:
                        print(f"[IMPEDANCE] No impedance data yet (EEG may have started)")
                time.sleep(0.5)  # Sample every 0.5 seconds
            
            print(f"[IMPEDANCE] Collection complete. Total samples: {len(self.impedance_samples)}")
        
        check_thread = threading.Thread(target=collect_impedance, daemon=True)
        check_thread.start()
        
        # Schedule end of impedance check
        self.phase_timer = threading.Timer(
            self.impedance_check_duration,
            self._impedance_check_complete
        )
        self.phase_timer.start()
        
        return True
    
    def _impedance_check_complete(self):
        """Impedance check completed"""
        # Note: Don't stop impedance stream here - it keeps running until EEG starts
        stats = self.get_impedance_stats(verbose=True)
        if stats["mean_kohm"] is not None:
            quality_msg = "Good" if stats["quality"] == "good" else "Poor - adjust earbuds"
            message = f"Impedance check complete. Mean: {stats['mean_kohm']:.1f} kΩ ({quality_msg})"
        else:
            message = "Impedance check complete. No impedance data collected."
        self._notify_phase_change(
            ProtocolPhase.IDLE,
            message,
            None
        )
    
    def start_paf_protocol(self, intervention_label: Optional[str] = None):
        """Start the PAF protocol sequence.
        
        New flow:
        1. Instructions (5s countdown)
        2. Eyes closed pre-baseline (30s)
        3. Intervention/recording (60s)
        4. Instructions (5s countdown)
        5. Eyes closed post-baseline (30s)
        6. Complete
        """
        if self.protocol_started:
            return False
        
        if not self.is_eeg_ready():
            self._notify_phase_change(
                ProtocolPhase.ERROR,
                "EEG stream not ready. Start EEG stream first."
            )
            return False
        
        self.intervention_label = intervention_label
        self.protocol_started = True
        self.protocol_complete = False
        self.baseline_complete = False
        
        # Mark start of recording
        if self.recorder.start_time is None:
            self.recorder.mark_start()
        
        # Start with pre-baseline instructions
        self._start_pre_baseline()
        
        return True
    
    def _start_pre_baseline(self):
        """Start pre-baseline phase with instructions"""
        print("[PROTOCOL] Starting pre-baseline instructions (5s countdown)")
        self._notify_phase_change(
            ProtocolPhase.INSTRUCTIONS_PRE,
            "Close your eyes and relax. Recording will begin in 5 seconds.",
            INSTRUCTION_DELAY
        )
        
        self.phase_timer = threading.Timer(
            INSTRUCTION_DELAY,
            self._phase_eyes_closed_pre_start
        )
        self.phase_timer.start()
        print(f"[PROTOCOL] Timer started for {INSTRUCTION_DELAY}s")
    
    def _phase_eyes_closed_pre_start(self):
        """Eyes closed pre-baseline begins (30s)"""
        print("[PROTOCOL] >>> EYES_CLOSED_PRE phase starting")
        self.recorder.mark_phase("eyes_closed_pre_start")
        
        self._notify_phase_change(
            ProtocolPhase.EYES_CLOSED_PRE,
            "Keep your eyes closed. Pre-baseline recording...",
            CLOSED_PRE_SEC
        )
        
        self.phase_timer = threading.Timer(
            CLOSED_PRE_SEC,
            self._phase_eyes_closed_pre_end
        )
        self.phase_timer.start()
        print(f"[PROTOCOL] Timer started for {CLOSED_PRE_SEC}s")
    
    def _phase_eyes_closed_pre_end(self):
        """Eyes closed pre-baseline ends"""
        print("[PROTOCOL] >>> EYES_CLOSED_PRE phase ending")
        self.recorder.mark_phase("eyes_closed_pre_end")
        beep()
        self.baseline_complete = True
        
        # Start intervention phase
        self._start_intervention_phase()
    
    def _start_intervention_phase(self):
        """Start the intervention/recording phase (60s)"""
        print("[PROTOCOL] >>> INTERVENTION phase starting")
        self.recorder.mark_phase("intervention_start")
        
        intervention_msg = f"Intervention: {self.intervention_label}" if self.intervention_label else "Recording in progress..."
        
        self._notify_phase_change(
            ProtocolPhase.INTERVENTION,
            intervention_msg,
            INTERVENTION_SEC
        )
        
        self.phase_timer = threading.Timer(
            INTERVENTION_SEC,
            self._phase_intervention_end
        )
        self.phase_timer.start()
        print(f"[PROTOCOL] Timer started for {INTERVENTION_SEC}s")
    
    def _phase_intervention_end(self):
        """Intervention phase ends"""
        print("[PROTOCOL] >>> INTERVENTION phase ending")
        self.recorder.mark_phase("intervention_end")
        beep()
        
        # Start post-baseline instructions
        self._start_post_baseline()
    
    def _start_post_baseline(self):
        """Start post-baseline phase with instructions"""
        print("[PROTOCOL] >>> INSTRUCTIONS_POST phase starting")
        self._notify_phase_change(
            ProtocolPhase.INSTRUCTIONS_POST,
            "Close your eyes again. Post-baseline recording in 5 seconds.",
            INSTRUCTION_DELAY
        )
        
        self.phase_timer = threading.Timer(
            INSTRUCTION_DELAY,
            self._phase_eyes_closed_post_start
        )
        self.phase_timer.start()
        print(f"[PROTOCOL] Timer started for {INSTRUCTION_DELAY}s")
    
    def _phase_eyes_closed_post_start(self):
        """Eyes closed post-baseline begins (30s)"""
        print("[PROTOCOL] >>> EYES_CLOSED_POST phase starting")
        self.recorder.mark_phase("eyes_closed_post_start")
        
        self._notify_phase_change(
            ProtocolPhase.EYES_CLOSED_POST,
            "Keep your eyes closed. Post-baseline recording...",
            CLOSED_POST_SEC
        )
        
        self.phase_timer = threading.Timer(
            CLOSED_POST_SEC,
            self._phase_eyes_closed_post_end
        )
        self.phase_timer.start()
        print(f"[PROTOCOL] Timer started for {CLOSED_POST_SEC}s")
    
    def _phase_eyes_closed_post_end(self):
        """Eyes closed post-baseline ends - protocol complete"""
        print("[PROTOCOL] >>> EYES_CLOSED_POST phase ending, protocol complete")
        self.recorder.mark_phase("eyes_closed_post_end")
        beep()
        
        self._protocol_complete()
    
    def complete_intervention(self):
        """Legacy method - intervention now auto-completes after 60s.
        Can be called to skip remaining intervention time."""
        if self.current_phase == ProtocolPhase.INTERVENTION:
            # Cancel current timer and move to post-baseline
            if self.phase_timer:
                self.phase_timer.cancel()
            self._phase_intervention_end()
            return True
        return False
    
    def _protocol_complete(self):
        """Protocol is complete"""
        self.protocol_complete = True
        self._notify_phase_change(
            ProtocolPhase.COMPLETE,
            "Protocol complete. You may open your eyes.",
            None
        )
    
    def stop_protocol(self):
        """Stop the protocol (abort)"""
        if self.phase_timer:
            self.phase_timer.cancel()
            self.phase_timer = None
        
        self.protocol_started = False
        self._notify_phase_change(
            ProtocolPhase.IDLE,
            "Protocol stopped.",
            None
        )
    
    def save_recording(self, intervention_label: Optional[str] = None) -> Dict[str, str]:
        """
        Save the current recording to CSV and JSON metadata files (like GUI does).
        Returns dict with 'csv_path' and 'json_path'
        """
        # Use provided label or stored one
        label = intervention_label or self.intervention_label
        
        samples, times, indices, st = self.recorder.snapshot()
        
        ensure_dir(self.out_dir)
        fname = f"eeg_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        csv_path = os.path.join(self.out_dir, fname + ".csv")
        json_path = os.path.join(self.out_dir, fname + ".json")
        
        # Save CSV
        import csv
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "ch1"])
            for t, v in samples:
                w.writerow([f"{t:.6f}", f"{v:.6f}"])
        
        # Save metadata JSON (like GUI does)
        impedance_values = []
        if self.controller and hasattr(self.controller, 'last_imp_values'):
            impedance_values = self.controller.last_imp_values
        
        # Add impedance check stats
        impedance_stats = self.get_impedance_stats()
        
        meta = {
            "fs": FS,
            "duration_sec": round(self.recorder.rel_sec(), 3),
            "mode": "intervention" if label else None,
            "intervention_label": label,
            "impedance_last_10": impedance_values,
            "impedance_check_stats": impedance_stats,
            **{
                k + "_sec": (round(v, 3) if v is not None else None)
                for k, v in times.items()
            },
            **indices,
        }
        
        with open(json_path, "w") as f:
            json.dump(meta, f, indent=2)
        
        print(f"[INFO] Saved CSV → {csv_path}")
        print(f"[INFO] Saved JSON → {json_path}")
        
        return {
            "csv_path": csv_path,
            "json_path": json_path,
            "filename": fname
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current protocol status"""
        status = {
            "phase": self.current_phase.value,
            "protocol_started": self.protocol_started,
            "protocol_complete": self.protocol_complete,
            "baseline_complete": self.baseline_complete,
            "eeg_ready": self.is_eeg_ready(),
            "impedance": self.get_impedance(),
            "impedance_stats": self.get_impedance_stats(),
            "sample_count": self.recorder.sample_count,
        }
        eeg_error = self.get_eeg_error()
        if eeg_error:
            status["eeg_error"] = eeg_error
        return status
    
    def cleanup(self):
        """Clean up resources"""
        print("[CLEANUP] Starting protocol service cleanup...")
        
        # Cancel any active timers
        if self.phase_timer:
            try:
                self.phase_timer.cancel()
            except Exception as e:
                print(f"[CLEANUP] Error canceling timer: {e}")
        
        # Stop the controller (this stops threads and event loops)
        if self.controller:
            try:
                self.controller.stop_all()
                print("[CLEANUP] Controller stopped")
            except Exception as e:
                print(f"[CLEANUP] Error stopping controller: {e}")
        
        self.protocol_started = False
        self.current_phase = ProtocolPhase.IDLE
        print("[CLEANUP] Protocol service cleanup complete")
