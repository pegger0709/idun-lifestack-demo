#!/usr/bin/env python

import argparse
import json
import os

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, welch, iirnotch, savgol_filter


# ---------- Filters ----------
# Basic preprocessing stack for in-ear EEG:
# - remove DC offset
# - optional notch (60Hz default, user override)
# - 1–40Hz bandpass to capture alpha cleanly (Guardian single-channel)
def bandpass_filter(x, fs, low, high, order=4):
    nyq = 0.5 * fs
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    return filtfilt(b, a, x)


def notch_filter(x, fs, freq, q=30.0):
    nyq = 0.5 * fs
    w0 = freq / nyq
    b, a = iirnotch(w0, q)
    return filtfilt(b, a, x)


def preprocess_eeg(x, fs, l_freq=1.0, h_freq=40.0, notch=None):
    # Center signal, apply notch (if set), then bandpass
    x = x.astype(float)
    x = x - np.mean(x)
    if notch not in (None, 0):
        x = notch_filter(x, fs, notch)
    x = bandpass_filter(x, fs, l_freq, h_freq)
    return x


# ---------- PAF / CoG ----------
# Compute peak alpha (PAF) and center-of-gravity alpha (CoG)
# Welch + optional smoothing is stable for short in-ear recordings.
def compute_paf_metrics(
    x,
    fs,
    alpha_low=8.0,
    alpha_high=13.0,
    nperseg_sec=2.0,
    noverlap_sec=1.0,
    smooth=True,
):
    if len(x) < int(fs * nperseg_sec):
        return {
            "paf_peak_hz": float("nan"),
            "paf_cog_hz": float("nan"),
            "alpha_power": float("nan"),
        }

    nperseg = int(fs * nperseg_sec)
    noverlap = int(fs * noverlap_sec)

    freqs, psd = welch(
        x,
        fs=fs,
        nperseg=nperseg,
        noverlap=noverlap,
        detrend=False,
    )

    # Light smoothing improves stability when alpha is broad/weak
    if smooth and len(psd) >= 11:
        psd = savgol_filter(psd, 11, 3)

    mask = (freqs >= alpha_low) & (freqs <= alpha_high)
    fa = freqs[mask]
    pa = psd[mask]

    if fa.size == 0 or np.all(pa <= 0):
        return {
            "paf_peak_hz": float("nan"),
            "paf_cog_hz": float("nan"),
            "alpha_power": float("nan"),
        }

    # Peak alpha
    peak_idx = np.argmax(pa)
    paf_peak = float(fa[peak_idx])

    # Center of gravity (more robust when peak is noisy)
    paf_cog = float(np.sum(fa * pa) / np.sum(pa))

    alpha_power = float(np.trapz(pa, fa))

    return {
        "paf_peak_hz": paf_peak,
        "paf_cog_hz": paf_cog,
        "alpha_power": alpha_power,
    }


# ---------- Artifact rejection ----------
# Two-part artifact pipeline tuned for Guardian:
# - robust z-score spike detection
# - high-frequency (20–40Hz) ratio check for muscle/motion noise
def robust_z_scores(x):
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    if mad == 0:
        std = np.std(x)
        if std == 0:
            return np.zeros_like(x)
        return (x - med) / std
    return (x - med) / (mad * 1.4826)


def window_indices(n_samples, win_len, step):
    starts = np.arange(0, max(n_samples - win_len + 1, 1), step, dtype=int)
    for s in starts:
        e = s + win_len
        if e > n_samples:
            break
        yield s, e


def compute_window_psd(x_win, fs):
    freqs, psd = welch(
        x_win,
        fs=fs,
        nperseg=len(x_win),
        noverlap=0,
        detrend=False,
    )
    return freqs, psd


def artifact_reject_segment(
    x,
    fs,
    z_thresh=5.0,
    hf_ratio_thresh=0.5,
    win_sec=2.0,
    overlap=0.5,
):
    # Reject windows with large spikes or high HF ratio (muscle)
    n_samples = len(x)
    if n_samples == 0:
        return np.array([]), {
            "n_windows": 0,
            "n_good_windows": 0,
            "prop_good_windows": 0.0,
            "prop_good_samples": 0.0,
        }

    z = robust_z_scores(x)

    win_len = int(fs * win_sec)
    step = int(win_len * (1.0 - overlap))
    if step <= 0:
        step = 1

    good_samples_mask = np.zeros(n_samples, dtype=bool)
    n_windows = 0
    n_good_windows = 0

    for start, end in window_indices(n_samples, win_len, step):
        n_windows += 1
        x_win = x[start:end]
        z_win = z[start:end]

        # Spike rejection
        if np.any(np.abs(z_win) > z_thresh):
            continue

        # HF noise rejection
        freqs, psd = compute_window_psd(x_win, fs)
        total_mask = (freqs >= 8.0) & (freqs <= 40.0)
        hf_mask = (freqs >= 20.0) & (freqs <= 40.0)

        total_power = np.trapz(psd[total_mask], freqs[total_mask]) if np.any(total_mask) else 0.0
        hf_power = np.trapz(psd[hf_mask], freqs[hf_mask]) if np.any(hf_mask) else 0.0

        hf_ratio = (hf_power / total_power) if total_power > 0 else 0.0

        if hf_ratio > hf_ratio_thresh:
            continue

        good_samples_mask[start:end] = True
        n_good_windows += 1

    if n_windows == 0 or n_good_windows == 0:
        return np.array([]), {
            "n_windows": int(n_windows),
            "n_good_windows": int(n_good_windows),
            "prop_good_windows": 0.0,
            "prop_good_samples": 0.0,
        }

    cleaned = x[good_samples_mask]
    prop_good_windows = n_good_windows / n_windows
    prop_good_samples = np.sum(good_samples_mask) / n_samples

    info = {
        "n_windows": int(n_windows),
        "n_good_windows": int(n_good_windows),
        "prop_good_windows": float(prop_good_windows),
        "prop_good_samples": float(prop_good_samples),
    }
    return cleaned, info


# ---------- IO and pipeline ----------

def load_metadata(path):
    with open(path, "r") as f:
        return json.load(f)


def _compute_segment_paf(x_raw, fs, alpha_low, alpha_high, l_freq, h_freq, notch,
                          z_thresh, hf_ratio_thresh, win_sec, overlap):
    """Helper to compute PAF metrics for a single segment"""
    # Preprocess
    x_proc = preprocess_eeg(x_raw, fs, l_freq=l_freq, h_freq=h_freq, notch=notch)
    
    # Artifact clean
    x_clean, art_info = artifact_reject_segment(
        x_proc, fs, z_thresh, hf_ratio_thresh, win_sec, overlap
    )
    
    # Compute PAF + CoG on clean data
    metrics = compute_paf_metrics(x_clean, fs, alpha_low, alpha_high)
    
    return {
        "n_samples_raw": int(len(x_raw)),
        "duration_sec_raw": float(len(x_raw) / fs),
        "n_samples_clean": int(len(x_clean)),
        "duration_sec_clean": float(len(x_clean) / fs),
        "artifact_rejection": art_info,
        **metrics,
    }


def _compute_hybrid_iaf(metrics):
    """Compute hybrid IAF from PAF metrics"""
    peak = metrics["paf_peak_hz"]
    cog = metrics["paf_cog_hz"]
    
    if not np.isnan(peak) and not np.isnan(cog):
        return 0.5 * (peak + cog), "hybrid_50_50"
    elif not np.isnan(peak):
        return peak, "peak_only"
    elif not np.isnan(cog):
        return cog, "cog_only"
    else:
        return float("nan"), "invalid"


def compute_paf_from_recording(
    csv_path,
    meta_path=None,
    eeg_col="ch1",
    alpha_low=8.0,
    alpha_high=13.0,
    l_freq=1.0,
    h_freq=40.0,
    notch=None,
    z_thresh=5.0,
    hf_ratio_thresh=0.5,
    win_sec=2.0,
    overlap=0.5,
    readiness_thresh_hz=9.5,
):
    """
    Compute PAF from a recording with the new protocol structure:
    - eyes_closed_pre (30s pre-baseline)
    - intervention (60s)
    - eyes_closed_post (30s post-baseline)
    
    Falls back to old protocol keys for backward compatibility.
    """
    # Load raw CSV and metadata
    if meta_path is None:
        base, _ = os.path.splitext(csv_path)
        meta_path = base + ".json"

    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"Metadata JSON not found: {meta_path}")

    df = pd.read_csv(csv_path)
    meta = load_metadata(meta_path)

    fs = float(meta["fs"])
    x_raw = df[eeg_col].values
    file_stem = os.path.splitext(os.path.basename(csv_path))[0]
    
    # Check if new protocol keys exist (eyes_closed_pre/post)
    has_new_protocol = "eyes_closed_pre_start_idx" in meta and "eyes_closed_post_start_idx" in meta
    
    if has_new_protocol:
        # New protocol: compute PAF from pre and post eyes-closed segments
        pre_start = int(meta["eyes_closed_pre_start_idx"])
        pre_end = int(meta["eyes_closed_pre_end_idx"])
        post_start = int(meta["eyes_closed_post_start_idx"])
        post_end = int(meta["eyes_closed_post_end_idx"])
        
        x_pre = x_raw[pre_start:pre_end]
        x_post = x_raw[post_start:post_end]
        
        # Compute PAF for both segments
        pre_results = _compute_segment_paf(
            x_pre, fs, alpha_low, alpha_high, l_freq, h_freq, notch,
            z_thresh, hf_ratio_thresh, win_sec, overlap
        )
        post_results = _compute_segment_paf(
            x_post, fs, alpha_low, alpha_high, l_freq, h_freq, notch,
            z_thresh, hf_ratio_thresh, win_sec, overlap
        )
        
        # Compute hybrid IAF for both
        pre_iaf, pre_source = _compute_hybrid_iaf(pre_results)
        post_iaf, post_source = _compute_hybrid_iaf(post_results)
        
        # Calculate delta PAF (post - pre)
        if not np.isnan(pre_iaf) and not np.isnan(post_iaf):
            delta_iaf = post_iaf - pre_iaf
        else:
            delta_iaf = float("nan")
        
        # Readiness classification based on post-intervention IAF
        if np.isnan(post_iaf):
            readiness_status = "INVALID"
        else:
            readiness_status = "READY" if post_iaf >= readiness_thresh_hz else "NOT_READY"
        
        result = {
            "file_stem": file_stem,
            "fs": fs,
            "protocol_type": "new_eyes_closed",
            "alpha_band_hz": [alpha_low, alpha_high],
            "eyes_closed_pre": pre_results,
            "eyes_closed_post": post_results,
            "pre_iaf_hz": float(pre_iaf) if not np.isnan(pre_iaf) else None,
            "pre_iaf_source": pre_source,
            "post_iaf_hz": float(post_iaf) if not np.isnan(post_iaf) else None,
            "post_iaf_source": post_source,
            "delta_iaf_hz": float(delta_iaf) if not np.isnan(delta_iaf) else None,
            "readiness_iaf_hz": float(post_iaf) if not np.isnan(post_iaf) else None,
            "readiness_source": post_source,
            "readiness_status": readiness_status,
            "readiness_thresh_hz": readiness_thresh_hz,
        }
        
        return result
    
    # Fall back to old protocol (eyes_open + eyes_closed)
    if "eyes_open_start_idx" not in meta:
        raise KeyError("Recording metadata missing required phase indices. "
                      "Expected either new protocol (eyes_closed_pre/post) or old protocol (eyes_open/closed).")
    
    # Old protocol: segment eyes-open and eyes-closed windows
    open_start = int(meta["eyes_open_start_idx"])
    open_end = int(meta["eyes_open_end_idx"])
    closed_start = int(meta["eyes_closed_start_idx"])
    closed_end = int(meta["eyes_closed_end_idx"])

    x_open = x_raw[open_start:open_end]
    x_closed = x_raw[closed_start:closed_end]

    # Compute PAF for both segments
    open_results = _compute_segment_paf(
        x_open, fs, alpha_low, alpha_high, l_freq, h_freq, notch,
        z_thresh, hf_ratio_thresh, win_sec, overlap
    )
    closed_results = _compute_segment_paf(
        x_closed, fs, alpha_low, alpha_high, l_freq, h_freq, notch,
        z_thresh, hf_ratio_thresh, win_sec, overlap
    )

    # Compute hybrid IAF from eyes-closed
    hybrid_iaf, readiness_source = _compute_hybrid_iaf(closed_results)

    # Readiness classification
    if np.isnan(hybrid_iaf):
        readiness_status = "INVALID"
    else:
        readiness_status = "READY" if hybrid_iaf >= readiness_thresh_hz else "NOT_READY"

    result = {
        "file_stem": file_stem,
        "fs": fs,
        "protocol_type": "old_eyes_open_closed",
        "alpha_band_hz": [alpha_low, alpha_high],
        "eyes_open": open_results,
        "eyes_closed": closed_results,
        "preprocessing": {
            "bandpass_hz": [l_freq, h_freq],
            "notch_hz": notch,
            "method": "butterworth_4th_order_zero_phase",
        },
        "artifact_settings": {
            "z_thresh": z_thresh,
            "hf_ratio_thresh": hf_ratio_thresh,
            "win_sec": win_sec,
            "overlap": overlap,
            "hf_band_hz": [20.0, 40.0],
            "total_band_for_ratio_hz": [8.0, 40.0],
        },
        "readiness_threshold_hz": readiness_thresh_hz,
        "readiness_iaf_hz": float(hybrid_iaf) if not np.isnan(hybrid_iaf) else None,
        "readiness_source": readiness_source,
        "readiness_status": readiness_status,
        "version": "paf_from_recording_v4",
    }

    return result


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(
        description="Compute PAF (peak & CoG) and readiness from IDUN Guardian recording."
    )
    parser.add_argument(
        "--notch",
        type=float,
        default=60.0,
        help="Notch frequency (default: 60 Hz). Use --notch 50 for EU/Asia, or --notch 0 to disable.",
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to EEG CSV (with 'timestamp' and 'ch1').",
    )
    parser.add_argument(
        "--meta",
        default=None,
        help="Path to metadata JSON (default: same basename as CSV).",
    )
    parser.add_argument(
        "--eeg_col",
        default="ch1",
        help="EEG column name in CSV (default: ch1).",
    )
    parser.add_argument(
        "--alpha_low",
        type=float,
        default=8.0,
        help="Lower alpha bound in Hz (default: 8.0).",
    )
    parser.add_argument(
        "--alpha_high",
        type=float,
        default=13.0,
        help="Upper alpha bound in Hz (default: 13.0).",
    )
    parser.add_argument(
        "--l_freq",
        type=float,
        default=1.0,
        help="High-pass cutoff in Hz (default: 1.0).",
    )
    parser.add_argument(
        "--h_freq",
        type=float,
        default=40.0,
        help="Low-pass cutoff in Hz (default: 40.0).",
    )
    parser.add_argument(
        "--z_thresh",
        type=float,
        default=5.0,
        help="Robust z-score threshold (default: 5.0).",
    )
    parser.add_argument(
        "--hf_ratio_thresh",
        type=float,
        default=0.5,
        help="HF(20–40)/total(8–40) power ratio threshold (default: 0.5).",
    )
    parser.add_argument(
        "--win_sec",
        type=float,
        default=2.0,
        help="Artifact window length in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=0.5,
        help="Artifact window overlap (0–1, default: 0.5).",
    )
    parser.add_argument(
        "--readiness_thresh_hz",
        type=float,
        default=9.5,
        help="Readiness threshold on closed IAF (default: 9.5 Hz).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: <csv_basename>_paf.json).",
    )

    args = parser.parse_args()

    result = compute_paf_from_recording(
        csv_path=args.csv,
        meta_path=args.meta,
        eeg_col=args.eeg_col,
        alpha_low=args.alpha_low,
        alpha_high=args.alpha_high,
        l_freq=args.l_freq,
        h_freq=args.h_freq,
        notch=args.notch,
        z_thresh=args.z_thresh,
        hf_ratio_thresh=args.hf_ratio_thresh,
        win_sec=args.win_sec,
        overlap=args.overlap,
        readiness_thresh_hz=args.readiness_thresh_hz,
    )

    print("=== PAF / Readiness ===")
    print(f"File: {result['file_stem']}")
    print(f"FS:   {result['fs']} Hz")
    print(f"Alpha band: {result['alpha_band_hz'][0]}–{result['alpha_band_hz'][1]} Hz")

    ec = result["eyes_closed"]
    print("\nEyes CLOSED:")
    print(
        f"  Raw:   {ec['duration_sec_raw']:.2f} s | "
        f"Clean: {ec['duration_sec_clean']:.2f} s "
        f"({ec['artifact_rejection']['prop_good_samples']*100:.1f}% kept)"
    )
    print(
        f"  PAF_peak: {ec['paf_peak_hz']:.2f} Hz | "
        f"PAF_CoG: {ec['paf_cog_hz']:.2f} Hz"
    )

    print("\nReadiness:")
    print(
        f"  IAF_used: {result['readiness_iaf_hz']} Hz "
        f"({result['readiness_source']}) | "
        f"Threshold: {result['readiness_threshold_hz']} Hz | "
        f"Status: {result['readiness_status']}"
    )

    out_path = args.out
    if out_path is None:
        base, _ = os.path.splitext(args.csv)
        out_path = base + "_paf.json"

    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nSaved JSON: {out_path}")


if __name__ == "__main__":
    main()
