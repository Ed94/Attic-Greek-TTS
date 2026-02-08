"""
Ancient Greek TTS — Spectral & Phonetic Analysis
Generates per-section spectrograms, pitch tracks, intensity contours,
and a summary report for phonetic review.
"""

import os
import re
import json
import numpy as np
import matplotlib.pyplot as plt
import parselmouth
from parselmouth.praat import call

try:
    import tomllib
except ImportError:
    import tomli as tomllib

# =============================================================================
# Configuration
# =============================================================================

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

OUTPUT_DIR   = config["tts"].get("output_dir", "output")
DEBUG_FILE   = config["files"].get("debug_file", "debug_dump.json")
ANALYSIS_DIR = "analysis"

VOICE_NAME    = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Enceladus")
SPEAKING_RATE = config["tts"].get("speaking_rate", 1.0)
AUDIO_ENC     = config["tts"].get("audio_encoding", "LINEAR16")
INPUT_PATH    = config["files"].get("input_text", "input.txt")
DELIMITER     = config["processing"].get("delimiter", "---")

EXT = "wav" if AUDIO_ENC == "LINEAR16" else "mp3"

# Pitch extraction parameters
PITCH_FLOOR    = 75
PITCH_CEILING  = 400
PITCH_TIMESTEP = 0.01

# Spectrogram parameters
SPEC_WINDOW    = 0.005
SPEC_MAX_FREQ  = 5000
SPEC_DYN_RANGE = 70

# =============================================================================
# File Discovery
# =============================================================================

def find_output_files():
    """
    Scans the output directory for audio files matching the naming convention.
    Returns a list of (section_index, filepath) sorted by section.
    """
    if not os.path.exists(OUTPUT_DIR):
        print(f"Output directory '{OUTPUT_DIR}' not found.")
        return []

    pattern = re.compile(rf'^(\d+)_.*\.{EXT}$')
    files = []

    for fname in os.listdir(OUTPUT_DIR):
        match = pattern.match(fname)
        if match:
            sec_idx = int(match.group(1))
            files.append((sec_idx, os.path.join(OUTPUT_DIR, fname)))

    files.sort(key=lambda x: x[0])
    return files

def load_debug_data():
    """Loads the debug dump and indexes it by section number."""
    if not os.path.exists(DEBUG_FILE):
        print(f"Debug file '{DEBUG_FILE}' not found. Word annotations will be unavailable.")
        return {}

    with open(DEBUG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    indexed = {}
    for entry in data:
        sec = entry.get("section")
        if sec is not None:
            indexed[sec] = entry.get("analysis", [])

    return indexed

def load_section_titles():
    """
    Reads the input text and extracts the first line of each section
    as a human-readable title.
    """
    if not os.path.exists(INPUT_PATH):
        return {}

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    sections = [s.strip() for s in content.split(DELIMITER) if s.strip()]
    titles = {}
    for i, sec in enumerate(sections):
        first_line = sec.split('\n')[0].strip()
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        titles[i + 1] = first_line

    return titles

# =============================================================================
# Core Analysis
# =============================================================================

def extract_pitch_stats(pitch):
    values = pitch.selected_array['frequency'].copy()
    values[values == 0] = np.nan
    voiced = values[~np.isnan(values)]

    if len(voiced) == 0:
        return {"mean": 0, "median": 0, "min": 0, "max": 0,
                "range": 0, "std": 0, "voiced_pct": 0}

    return {
        "mean":       round(float(np.mean(voiced)), 1),
        "median":     round(float(np.median(voiced)), 1),
        "min":        round(float(np.min(voiced)), 1),
        "max":        round(float(np.max(voiced)), 1),
        "range":      round(float(np.max(voiced) - np.min(voiced)), 1),
        "std":        round(float(np.std(voiced)), 1),
        "voiced_pct": round(float(len(voiced) / len(values) * 100), 1),
    }

def measure_pitch_peaks(pitch):
    values = pitch.selected_array['frequency'].copy()
    times  = pitch.xs()
    values[values == 0] = np.nan

    peaks = []
    for i in range(2, len(values) - 2):
        if np.isnan(values[i]):
            continue
        window = values[i-2:i+3]
        if np.isnan(window).any():
            continue
        if values[i] == np.max(window) and values[i] > np.min(window) + 10:
            peaks.append({
                "time": round(float(times[i]), 3),
                "f0":   round(float(values[i]), 1),
            })
    return peaks

def measure_intensity_stats(intensity):
    values = intensity.values[0]
    return {
        "mean": round(float(np.mean(values)), 1),
        "max":  round(float(np.max(values)), 1),
        "min":  round(float(np.min(values)), 1),
        "std":  round(float(np.std(values)), 1),
    }

def measure_pitch_intensity_correlation(pitch, intensity):
    p_values = pitch.selected_array['frequency'].copy()
    p_times  = pitch.xs()
    i_times  = intensity.xs()
    i_values = intensity.values[0]

    i_interp = np.interp(p_times, i_times, i_values)

    voiced_mask = p_values > 0
    if np.sum(voiced_mask) < 10:
        return 0.0

    p_voiced = p_values[voiced_mask]
    i_voiced = i_interp[voiced_mask]

    if np.std(p_voiced) < 1e-6 or np.std(i_voiced) < 1e-6:
        return 0.0

    corr = np.corrcoef(p_voiced, i_voiced)[0, 1]
    return round(float(corr), 3)

def measure_downdrift(pitch, num_segments=4):
    values = pitch.selected_array['frequency'].copy()
    values[values == 0] = np.nan

    segment_len = len(values) // num_segments
    if segment_len == 0:
        return []

    segments = []
    for i in range(num_segments):
        start = i * segment_len
        end   = start + segment_len
        seg   = values[start:end]
        voiced = seg[~np.isnan(seg)]
        if len(voiced) > 0:
            segments.append({
                "segment": i + 1,
                "mean_f0": round(float(np.mean(voiced)), 1),
                "peak_f0": round(float(np.max(voiced)), 1),
            })
        else:
            segments.append({"segment": i + 1, "mean_f0": 0, "peak_f0": 0})
    return segments

def detect_silence_durations(snd, threshold_db=45):
    intensity = snd.to_intensity(minimum_pitch=PITCH_FLOOR)
    times     = intensity.xs()
    values    = intensity.values[0]

    silences = []
    in_silence = False
    start = 0

    for i, val in enumerate(values):
        if val < threshold_db:
            if not in_silence:
                in_silence = True
                start = times[i]
        else:
            if in_silence:
                duration = times[i] - start
                if duration > 0.01:
                    silences.append({
                        "start":    round(start, 3),
                        "end":      round(float(times[i]), 3),
                        "duration": round(duration, 3),
                    })
                in_silence = False
    return silences

def count_accent_types(debug_entries):
    """Counts accent types from debug data for a section."""
    counts = {"acute": 0, "circumflex": 0, "grave": 0, "none": 0}
    for entry in debug_entries:
        a = entry.get("accent", "none")
        if a in counts:
            counts[a] += 1
    return counts

# =============================================================================
# Visualization
# =============================================================================

def generate_full_analysis(wav_path, output_prefix, title="", debug_data=None):
    snd = parselmouth.Sound(wav_path)

    spectrogram = snd.to_spectrogram(
        window_length=SPEC_WINDOW,
        maximum_frequency=SPEC_MAX_FREQ
    )
    pitch = snd.to_pitch(
        time_step=PITCH_TIMESTEP,
        pitch_floor=PITCH_FLOOR,
        pitch_ceiling=PITCH_CEILING
    )
    intensity = snd.to_intensity(minimum_pitch=PITCH_FLOOR)

    # Metrics
    pitch_stats    = extract_pitch_stats(pitch)
    int_stats      = measure_intensity_stats(intensity)
    correlation    = measure_pitch_intensity_correlation(pitch, intensity)
    peaks          = measure_pitch_peaks(pitch)
    downdrift_segs = measure_downdrift(pitch)
    silences       = detect_silence_durations(snd)
    accent_counts  = count_accent_types(debug_data) if debug_data else {}

    metrics = {
        "file":              os.path.basename(wav_path),
        "duration_s":        round(snd.duration, 2),
        "pitch":             pitch_stats,
        "intensity":         int_stats,
        "f0_intensity_corr": correlation,
        "accent_peaks":      peaks,
        "accent_counts":     accent_counts,
        "downdrift":         downdrift_segs,
        "silence_regions":   silences,
    }

    # Arrays
    pitch_values = pitch.selected_array['frequency'].copy()
    pitch_times  = pitch.xs()
    pitch_values[pitch_values == 0] = np.nan

    int_times  = intensity.xs()
    int_values = intensity.values[0]

    sg_db = 10 * np.log10(spectrogram.values + 1e-20)

    has_words = debug_data and any(e.get("greek") for e in debug_data)
    n_panels  = 4 if has_words else 3

    fig, axes = plt.subplots(n_panels, 1, figsize=(15, 4 * n_panels), sharex=True)
    fig.suptitle(title, fontsize=14, fontweight='bold')

    # Panel 1: Spectrogram + F0
    ax1 = axes[0]
    ax1.imshow(sg_db, origin='lower', aspect='auto',
               extent=[spectrogram.xmin, spectrogram.xmax,
                       spectrogram.ymin, spectrogram.ymax],
               vmin=sg_db.max() - SPEC_DYN_RANGE, cmap='Greys')
    ax1.set_ylabel('Frequency (Hz)')
    ax1.set_ylim(0, SPEC_MAX_FREQ)

    ax1_f0 = ax1.twinx()
    ax1_f0.plot(pitch_times, pitch_values, 'r-', linewidth=2, alpha=0.85)
    ax1_f0.set_ylabel('F0 (Hz)', color='r')
    ax1_f0.set_ylim(PITCH_FLOOR - 25, PITCH_CEILING)
    ax1.set_title('Spectrogram + Pitch Track')

    # Panel 2: Intensity
    ax2 = axes[1]
    ax2.plot(int_times, int_values, 'b-', linewidth=1.2)
    ax2.set_ylabel('Intensity (dB)')
    corr_label = "pitch accent ✓" if abs(correlation) < 0.4 else "stress leaking ⚠"
    ax2.set_title(f'Intensity  |  F0-Intensity r={correlation:.3f} ({corr_label})')
    ax2.grid(True, alpha=0.2)

    # Panel 3: Pitch track
    ax3 = axes[2]
    ax3.plot(pitch_times, pitch_values, 'r-', linewidth=2)
    ax3.set_ylabel('F0 (Hz)')
    ax3.set_ylim(PITCH_FLOOR - 25, PITCH_CEILING)
    ax3.grid(True, alpha=0.3)

    if len(downdrift_segs) >= 2:
        seg_times = np.linspace(pitch_times[0], pitch_times[-1], len(downdrift_segs))
        seg_means = [s["mean_f0"] for s in downdrift_segs]
        ax3.plot(seg_times, seg_means, 'g--', linewidth=2, alpha=0.7, label='Downdrift trend')
        ax3.legend(loc='upper right')

    for pk in peaks:
        ax3.plot(pk["time"], pk["f0"], 'rv', markersize=6, alpha=0.6)

    ax3.set_title(f'Pitch Track  |  mean={pitch_stats["mean"]}Hz  '
                  f'range={pitch_stats["range"]}Hz  '
                  f'peaks={len(peaks)}')

    # Panel 4: Word annotations
    if has_words:
        ax4 = axes[3]
        ax4.set_xlim(ax1.get_xlim())
        ax4.set_ylim(0, 1)
        ax4.set_yticks([])
        ax4.set_xlabel('Time (s)')

        accent_str = "  ".join(f"{k}={v}" for k, v in accent_counts.items()) if accent_counts else ""
        ax4.set_title(f'Word Annotations  |  {accent_str}')

        word_entries = [e for e in debug_data if e.get("greek")]
        if word_entries:
            duration = snd.duration
            word_dur = duration / len(word_entries)

            colors = {
                "acute":      "#CC0000",
                "circumflex": "#0066CC",
                "grave":      "#666666",
                "none":       "#999999"
            }

            for w_idx, entry in enumerate(word_entries):
                t_center = (w_idx + 0.5) * word_dur
                greek    = entry.get("greek", "")
                accent   = entry.get("accent", "none")
                ipa      = entry.get("ipa", "")
                color    = colors.get(accent, "#999999")

                ax4.axvline(x=w_idx * word_dur, color='#CCCCCC',
                           linewidth=0.5, linestyle='-')
                ax4.text(t_center, 0.75, greek,
                        ha='center', va='center', fontsize=7,
                        color=color, fontweight='bold')
                ax4.text(t_center, 0.45, f'/{ipa}/',
                        ha='center', va='center', fontsize=5.5,
                        color='#444444', fontstyle='italic')
                ax4.text(t_center, 0.2, accent,
                        ha='center', va='center', fontsize=5.5,
                        color=color)
    else:
        axes[-1].set_xlabel('Time (s)')

    plt.tight_layout()

    png_path = f"{output_prefix}.png"
    plt.savefig(png_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {png_path}")

    return metrics

# =============================================================================
# Report Generation
# =============================================================================

def generate_summary_report(all_metrics, section_titles, output_path):
    lines = []
    lines.append("=" * 90)
    lines.append("  ANCIENT GREEK TTS — PHONETIC ANALYSIS REPORT")
    lines.append("=" * 90)
    lines.append("")
    lines.append(f"  Voice:         {VOICE_NAME}")
    lines.append(f"  Speaking Rate: {SPEAKING_RATE}")
    lines.append(f"  Encoding:      {AUDIO_ENC}")
    lines.append(f"  Sections:      {len(all_metrics)}")
    lines.append("")

    # Prosody config echo — so the reviewer knows what parameters produced this output
    prosody = config.get("prosody", {})
    lines.append("  PROSODY CONFIGURATION")
    lines.append("  " + "-" * 40)
    for key in sorted(prosody.keys()):
        lines.append(f"    {key:40s} = {prosody[key]}")
    lines.append("")

    pauses = config.get("pauses", {})
    lines.append("  PAUSE CONFIGURATION")
    lines.append("  " + "-" * 40)
    for key in sorted(pauses.keys()):
        lines.append(f"    {key:40s} = {pauses[key]}")
    lines.append("")

    pacing = config.get("pacing", {})
    lines.append("  PACING CONFIGURATION")
    lines.append("  " + "-" * 40)
    for key in sorted(pacing.keys()):
        lines.append(f"    {key:40s} = {pacing[key]}")
    lines.append("")

    # Global aggregates
    all_corrs   = [m["f0_intensity_corr"] for m in all_metrics]
    all_ranges  = [m["pitch"]["range"] for m in all_metrics if m["pitch"]["range"] > 0]
    all_means   = [m["pitch"]["mean"] for m in all_metrics if m["pitch"]["mean"] > 0]
    total_peaks = sum(len(m["accent_peaks"]) for m in all_metrics)
    total_dur   = sum(m["duration_s"] for m in all_metrics)

    total_accents = {"acute": 0, "circumflex": 0, "grave": 0, "none": 0}
    for m in all_metrics:
        for k, v in m.get("accent_counts", {}).items():
            if k in total_accents:
                total_accents[k] += v

    lines.append("  GLOBAL STATISTICS")
    lines.append("  " + "-" * 40)
    lines.append(f"    Total duration:              {total_dur:.1f}s")
    lines.append(f"    Total F0 peaks detected:     {total_peaks}")
    if all_means:
        lines.append(f"    Mean F0 across sections:     {np.mean(all_means):.1f} Hz")
    if all_ranges:
        lines.append(f"    Mean F0 range:               {np.mean(all_ranges):.1f} Hz")
    lines.append(f"    Mean F0-intensity corr:      {np.mean(all_corrs):.3f}")
    lines.append("")

    lines.append("  ACCENT DISTRIBUTION (from debug data)")
    lines.append("  " + "-" * 40)
    total_words = sum(total_accents.values())
    for k in ["acute", "circumflex", "grave", "none"]:
        count = total_accents[k]
        pct = (count / total_words * 100) if total_words > 0 else 0
        lines.append(f"    {k:15s}  {count:5d}  ({pct:5.1f}%)")
    lines.append("")

    # Pitch accent vs stress accent assessment
    lines.append("  PITCH ACCENT ASSESSMENT")
    lines.append("  " + "-" * 40)
    mean_corr = np.mean(all_corrs)
    if abs(mean_corr) < 0.2:
        verdict = "EXCELLENT — F0 and intensity are decoupled. Pure pitch accent."
    elif abs(mean_corr) < 0.4:
        verdict = "GOOD — Mostly pitch accent with minor intensity correlation."
    elif abs(mean_corr) < 0.6:
        verdict = "MARGINAL — Noticeable stress accent leaking through."
    else:
        verdict = "POOR — Strong F0-intensity coupling. Sounds like stress accent."
    lines.append(f"    Mean |r| = {abs(mean_corr):.3f}  →  {verdict}")
    lines.append("")

    # Downdrift assessment
    lines.append("  DOWNDRIFT ASSESSMENT")
    lines.append("  " + "-" * 40)
    drift_scores = []
    for m in all_metrics:
        segs = m.get("downdrift", [])
        if len(segs) >= 2:
            first_mean = segs[0]["mean_f0"]
            last_mean  = segs[-1]["mean_f0"]
            if first_mean > 0:
                decline_pct = (first_mean - last_mean) / first_mean * 100
                drift_scores.append(decline_pct)

    if drift_scores:
        mean_decline = np.mean(drift_scores)
        lines.append(f"    Mean F0 decline (first→last quarter): {mean_decline:.1f}%")
        if mean_decline > 15:
            lines.append(f"    Assessment: Strong downdrift. May be too steep for short sentences.")
        elif mean_decline > 5:
            lines.append(f"    Assessment: Moderate downdrift. Natural-sounding declination.")
        elif mean_decline > 0:
            lines.append(f"    Assessment: Weak downdrift. Consider widening start/end spread.")
        else:
            lines.append(f"    Assessment: No downdrift detected. Check configuration.")
    lines.append("")

    # Per-section detail
    lines.append("=" * 90)
    lines.append("  PER-SECTION DETAIL")
    lines.append("=" * 90)

    for m in all_metrics:
        sec_num = m.get("section", "?")
        title   = section_titles.get(sec_num, "")
        lines.append("")
        lines.append(f"  Section {sec_num}: {title}")
        lines.append("  " + "-" * 60)
        lines.append(f"    File:       {m['file']}")
        lines.append(f"    Duration:   {m['duration_s']}s")
        lines.append(f"    F0:         mean={m['pitch']['mean']}  median={m['pitch']['median']}  "
                     f"range={m['pitch']['range']}  std={m['pitch']['std']}")
        lines.append(f"    Intensity:  mean={m['intensity']['mean']}  max={m['intensity']['max']}  "
                     f"std={m['intensity']['std']}")
        lines.append(f"    F0-Int r:   {m['f0_intensity_corr']}")
        lines.append(f"    Peaks:      {len(m['accent_peaks'])}")
        lines.append(f"    Silences:   {len(m['silence_regions'])} regions")

        if m.get("accent_counts"):
            ac = m["accent_counts"]
            lines.append(f"    Accents:    acute={ac.get('acute',0)}  circ={ac.get('circumflex',0)}  "
                        f"grave={ac.get('grave',0)}  none={ac.get('none',0)}")

        # Downdrift for this section
        segs = m.get("downdrift", [])
        if segs:
            seg_str = "  →  ".join(f"Q{s['segment']}:{s['mean_f0']}Hz" for s in segs)
            lines.append(f"    Downdrift:  {seg_str}")

        # Flag anomalies
        if m['pitch']['max'] > 350:
            lines.append(f"    ⚠ SPIKE: F0 max {m['pitch']['max']}Hz exceeds 350Hz — possible tracking error or voice overshoot")
        if abs(m['f0_intensity_corr']) > 0.5:
            lines.append(f"    ⚠ STRESS LEAK: High F0-intensity correlation ({m['f0_intensity_corr']})")
        if m['pitch']['voiced_pct'] < 50:
            lines.append(f"    ⚠ LOW VOICING: Only {m['pitch']['voiced_pct']}% voiced frames — many unvoiced segments")

    lines.append("")
    lines.append("=" * 90)
    lines.append("  END OF REPORT")
    lines.append("=" * 90)

    report_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"  Report saved: {output_path}")

    return report_text

# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    os.makedirs(ANALYSIS_DIR, exist_ok=True)

    audio_files    = find_output_files()
    debug_data     = load_debug_data()
    section_titles = load_section_titles()

    if not audio_files:
        print("No audio files found. Run the TTS generator first.")
        exit(1)

    print(f"Found {len(audio_files)} sections to analyze.")
    print(f"Debug data available for sections: {sorted(debug_data.keys())}")
    print()

    all_metrics = []

    for sec_idx, wav_path in audio_files:
        title = section_titles.get(sec_idx, f"Section {sec_idx}")
        title = f"Section {sec_idx}: {title}"

        print(f"Analyzing {title}...")

        sec_debug = debug_data.get(sec_idx, None)
        output_prefix = os.path.join(ANALYSIS_DIR, f"section_{sec_idx:02d}")

        metrics = generate_full_analysis(
            wav_path,
            output_prefix,
            title=title,
            debug_data=sec_debug
        )
        metrics["section"] = sec_idx
        all_metrics.append(metrics)

    # Generate reports
    print()
    print("Generating summary report...")

    report_path = os.path.join(ANALYSIS_DIR, "phonetic_report.txt")
    report_text = generate_summary_report(all_metrics, section_titles, report_path)

    metrics_path = os.path.join(ANALYSIS_DIR, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2, ensure_ascii=False)
    print(f"  Metrics saved: {metrics_path}")

    # Print summary to console
    print()
    print(report_text)
