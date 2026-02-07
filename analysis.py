import parselmouth
from parselmouth.praat import call
import matplotlib.pyplot as plt
import numpy as np

def generate_analysis_image(wav_path, output_path, title=""):
	snd = parselmouth.Sound(wav_path)
	
	# Wideband spectrogram (good for formants and consonant detail)
	spectrogram = snd.to_spectrogram(window_length=0.005, maximum_frequency=5000)
	
	# Pitch track (this is the critical one for accent verification)
	pitch = snd.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=400)
	
	# Intensity contour (helps distinguish stress from pitch)
	intensity = snd.to_intensity(minimum_pitch=75)
	
	fig, axes = plt.subplots(3, 1, figsize=(16, 10), sharex=True)
	fig.suptitle(title, fontsize=14)
	
	# Panel 1: Spectrogram + pitch overlay
	ax1 = axes[0]
	sg_db = 10 * np.log10(spectrogram.values + 1e-20)
	ax1.imshow(sg_db, origin='lower', aspect='auto',
			extent=[spectrogram.xmin, spectrogram.xmax, 
					spectrogram.ymin, spectrogram.ymax],
			vmin=sg_db.max() - 70, cmap='Greys')

	
	# Overlay pitch as red line
	pitch_values = pitch.selected_array['frequency']
	pitch_times = pitch.xs()
	pitch_values[pitch_values == 0] = np.nan  # unvoiced frames
	ax1_pitch = ax1.twinx()
	ax1_pitch.plot(pitch_times, pitch_values, 'r-', linewidth=2, label='F0 (pitch)')
	ax1_pitch.set_ylabel('F0 (Hz)', color='r')
	ax1_pitch.set_ylim(50, 350)
	ax1.set_ylabel('Frequency (Hz)')
	ax1.set_title('Spectrogram + Pitch Track')
	
	# Panel 2: Intensity
	ax2 = axes[1]
	int_times = intensity.xs()
	int_values = intensity.values[0]
	ax2.plot(int_times, int_values, 'b-', linewidth=1.5)
	ax2.set_ylabel('Intensity (dB)')
	ax2.set_title('Intensity (loudness — should NOT correlate with accent if pitch accent is working)')
	
	# Panel 3: Pitch alone (cleaner view)
	ax3 = axes[2]
	ax3.plot(pitch_times, pitch_values, 'r-', linewidth=2)
	ax3.set_ylabel('F0 (Hz)')
	ax3.set_xlabel('Time (s)')
	ax3.set_title('Pitch Track (F0) — accent peaks should be visible here')
	ax3.set_ylim(50, 350)
	ax3.grid(True, alpha=0.3)
	
	plt.tight_layout()
	plt.savefig(output_path, dpi=150)
	plt.close()
	print(f"Saved: {output_path}")


if __name__ == "__main__":
	# Section 17 — iota subscript words (circumflex vs acute)
	generate_analysis_image("output/17_t_ad_t_hads_log_tim_d_de-DE-Chirp3-HD-Gacrux_0.9.wav", "spectrals/analysis_subscript.png", 
							title="Section 17: Iota Subscript — τῇ ᾄδω τῷ ᾅδης λόγῳ τιμῇ ᾠδή")

	# Section 20 — τίς vs τὶς  
	generate_analysis_image("output/20_tis_estin_tis_anthrpos_de-DE-Chirp3-HD-Gacrux_0.9.wav", "spectrals/analysis_tis.png",
							title="Section 20: τίς ἐστιν; τὶς ἄνθρωπος")

	# Section 5 — long passage with downdrift and interrogative
	generate_analysis_image("output/05_noson_toinyn_n_d_eg_hsper_legomen_de-DE-Chirp3-HD-Gacrux_0.9.wav", "spectrals/analysis_downdrift.png",
							title="Section 5: Divided Line — downdrift + interrogative")

	# Section 16 — geminates
	generate_analysis_image("output/16_alla_allos_thalatta_attik_gramma_hippos_de-DE-Chirp3-HD-Gacrux_0.9.wav", "spectrals/analysis_geminates.png",
							title="Section 16: Geminates — ἀλλά θάλαττα γράμμα ἵππος")
