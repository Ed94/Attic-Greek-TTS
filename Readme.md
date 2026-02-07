# Attic Greek TTS using Google's Chrip 3

idk if this is accurate. This was done in an afternoon. Used gemini 3 pro for the attic rules. I made this after watching some podcasts and being confused on why tts was so behind for older langauges.

Κατέβην χθὲς εἰς Πειραιᾶ μετὰ Γλαύκωνος τοῦ Ἀρίστωνος
ἄνδρα μοι ἔννεπε, μοῦσα, πολύτροπον

See the [pipeline documentation](./PIPELINE.md) in the script header for the full technical breakdown.

## How It Works

Greek text goes in, spoken Attic Greek audio comes out. The TTS engine is treated as a dumb waveform synthesizer — the visible text inside each SSML `<phoneme>` tag is a Romanized decoy. The actual audio is forced by IPA strings computed from CLTK's Probert reconstruction. Pitch accent is imposed externally via `<prosody contour>` tags. The engine never makes a single phonological decision.

### Phonology

- **IPA Transcription** via CLTK (Probert reconstruction, Attic dialect) with post-transcription corrections
- **Vowel Quantity Enforcement** from Greek source characters — η/ω always long, circumflex-bearing vowels always long, explicit macrons (ᾱ, ῑ, ῡ) honored
- **Gamma Nasalization** — γγ → [ŋɡ], γκ → [ŋk], γχ → [ŋx], γξ → [ŋks], with de-nasalization before non-velars (γν → [gn], γμ → [gm])
- **Rough Breathing** — dasia on vowels produces [h], rho with dasia (ῥ) produces aspirated trill
- **IPA Normalization** — uvular/approximant r-sounds replaced with alveolar trill [r]

### Pitch Accent

- **Acute** — sharp rise to configurable peak, then fall
- **Circumflex** — rise-fall bounded within the accented syllable's duration
- **Grave** — suppressed rise, modeling pitch neutralization on non-final words
- **Accent detection** from NFD-decomposed Greek source text, never from IPA
- **Syllable-aware contour timing** — accent position calculated from vowel nuclei, not raw character index

### Prosody

- **Downdrift** — linear pitch declination across sentences with clause-boundary resets at commas, colons, and medial stops
- **Interrogative Intonation** — sentences ending in ; (Greek question mark) get a rising terminal
- **Prosodic Grouping** — proclitics, enclitics, and elided forms merged into groups before SSML generation
- **Breath Pacing** — natural pauses inserted at conjunctions and prepositions when phrase length exceeds a threshold
- **Heavy Word Rate Modulation** — words with long vowels slowed proportionally to syllable count

## Input Format

The input file uses `---` as a section delimiter. Each section produces its own WAV file. See [input.txt](./input.txt) for examples.

## Prerequisites

- Python 3.9+
- A Google Cloud account with the Text-to-Speech API enabled
- CLTK Greek models (`grc_models_cltk`)

## Setup

Install dependencies:

```ps1
uv sync
```

If CLTK models are missing, the script will print download instructions on first run.

### Google Cloud Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable the **Cloud Text-to-Speech API** (search for it in the marketplace)
4. Go to **APIs & Services → Credentials**
5. Click **+ CREATE CREDENTIALS → Service Account**
6. Name it (e.g., `tts-runner`), assign **Basic → Editor** role
7. Click on the service account → **KEYS** tab → **ADD KEY → Create new key → JSON**
8. Save the downloaded file as `service-account.json` in the project root
9. Set the path in `config.toml` under `[google_cloud]`

Google offers a free tier (usually 1M characters/month for standard voices, less for Chirp). Billing must be enabled for verification.

## Configuration

All tunable parameters are in [config.toml](./config.toml) — prosody values, pause durations, pacing thresholds, voice selection, and file paths.

## Output

output/ 01_katebn_chthes_.wav 02_andra_moi_.wav … playlist.m3u

- `debug_dump.json`: Full SSML, per-word IPA, accent mapping, contour strings.
- `transcription_cache.json`: Cached CLTK transcriptions (auto-invalidated on config change).

## Analysis

`analysis.py` generates per-section spectrograms with pitch tracks, intensity contours, word annotations, and a phonetic summary report:

```ps1
uv run analysis.py
```

analysis/ section_01.png:

- 4-panel spectrogram + F0 + intensity + word annotations section_02.png … phonetic_report.txt metrics.json

## Why German?

German's phoneme inventory is the closest match to reconstructed Attic Greek among available TTS voices:

| Feature | German | English | Greek (Modern) |
|---|---|---|---|
| Monophthong vowels | ✓ pure | ✗ diphthongized | ✓ but wrong qualities |
| Aspirated voiceless stops | ✓ | partial | ✗ |
| Alveolar trill [r] | ✓ | ✗ approximant [ɹ] | ✓ |
| Pitch vs stress | neutral | strong stress | strong stress |

English models diphthongize vowels, use approximant /ɹ/, and flap /t/. Modern Greek models impose stress accent and monophthongize diphthongs. The German voice is a neutral canvas.

