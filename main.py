"""
================================================================================
A N C I E N T   G R E E K   T T S   G E N E R A T O R
The "German Trojan Horse" Method
================================================================================

Google Cloud TTS does not support Ancient Greek. Feeding it Greek script
produces Modern Greek pronunciation — wrong vowels, stress accent instead
of pitch accent, monophthongized diphthongs — or outright failure.

The solution is to never let the engine see Greek at all.

We treat the TTS engine as a dumb waveform synthesizer. Every phonological
decision — vowel quality, vowel quantity, aspiration, pitch accent, sentence
intonation — is computed offline and injected via SSML. The engine's only
job is to turn IPA into sound. We use a German voice model because its
phoneme inventory (pure monophthong vowels, aspirated voiceless stops,
alveolar trill, clean fricatives) maps to reconstructed Attic Greek with
far less distortion than English models, whose diphthongized vowels,
approximant /ɹ/, and flapped /t/ would be catastrophic.

The bypass works as follows: the visible text inside each SSML <phoneme>
tag is a Romanized decoy (e.g., "Mênin aeide") that satisfies the XML
parser's content requirement. The actual audio is forced by the IPA string
in the 'ph' attribute. Pitch accent is imposed externally via <prosody
contour> tags computed from the Greek source text. The engine never makes
a single phonological decision — it is a puppet.

================================================================================
P I P E L I N E
================================================================================

[1] NORMALIZATION & SAFETY
    │
    │  Normalizes Greek-specific punctuation codepoints before any other
    │  processing: U+037E (Greek question mark) → U+003B (ASCII semi-
    │  colon), U+0387 (ano teleia) → U+00B7 (middle dot). This ensures
    │  sentence splitting and interrogative detection work regardless
    │  of which visually-identical codepoint the source text uses.
    │
    │  Cleans critical sigla: removes {}, [], <>, †.
    │  Expands Arabic numerals ("24" → εἴκοσι τέτταρες) and Roman
    │  numerals ("IV" → τέτταρες) into spelled-out Greek. Roman numeral
    │  detection uses an explicit whitelist from ROMAN_MAP and guards
    │  against false positives with a comprehensive Unicode adjacency
    │  check spanning the full Greek and Coptic (U+0370–03FF), Greek
    │  Extended (U+1F00–1FFF), and Combining Diacritical (U+0300–036F)
    │  blocks.
    │  Escapes XML special characters to prevent API crashes.
    │
    ▼
[2] SENTENCE ANALYSIS — The "Downdrift" Engine
    │
    │  Models the intonation contour of each sentence as a linear pitch
    │  declination from a configurable start offset to a configurable
    │  end offset (default: +10% → −10%). This approximates the
    │  well-attested downdrift phenomenon in Ancient Greek prose.
    │
    │  Interrogative Detection: Sentences ending in the Greek question
    │  mark (;) — normalized to ASCII semicolon in [1] — invert the
    │  slope to an "updrift" contour (default: −5% → +10%), producing
    │  a rising terminal.
    │
    │  Clause Boundary Reset: At commas, colons, and medial stops (·),
    │  the baseline rewinds by a configurable fraction of the sentence
    │  length (downdrift_clause_based_rewind_scale), simulating the
    │  partial intonation reset observed at clause boundaries in
    │  reconstructed delivery.
    │
    ▼
[3] PHONOLOGY ENGINE (Cached)
    │
    │  Transcribes each word to IPA via CLTK (Probert reconstruction,
    │  Attic dialect). Results are cached in transcription_cache.json
    │  with automatic invalidation when config.toml or the script
    │  itself changes (MD5 comparison). The cache is saved atomically
    │  after every section and incrementally every 50 new entries to
    │  prevent data loss during long batches or mid-section crashes.
    │
    │  Post-transcription corrections:
    │
    │  ● Source-Driven Quantity Enforcement: The engine uses a shared
    │    vowel-unit scanner (scan_greek_vowel_units) to walk the Greek
    │    source in parallel with the IPA string, identifying which
    │    vowels are inherently long. Length is determined from three
    │    sources: inherently long graphemes (η, ω), circumflex accent
    │    (perispomeni — a circumflex can only appear on a long vowel,
    │    so its presence guarantees length even on α, ι, υ), and
    │    explicit macrons (combining macron U+0304, as in ᾱ, ῑ, ῡ).
    │    The length marker (ː) is applied only to positions confirmed
    │    long by one of these three signals. Diphthongs are collapsed
    │    into single vocalic units using the same scanner that accent
    │    mapping uses, ensuring consistent alignment everywhere. Short
    │    vowels that happen to share an IPA symbol are left untouched.
    │
    │  ● Gamma Nasalization: Enforces [ŋ] before velars — γγ → [ŋɡ],
    │    γκ → [ŋk], γχ → [ŋx], γξ → [ŋks]. Handles both CLTK's
    │    expanded (gks) and unexpanded (gξ) representations of xi.
    │    Longest-match-first ordering prevents partial replacements.
    │    De-nasalizes gamma before non-velar consonants where CLTK
    │    over-nasalizes: γν → [gn], γμ → [gm], γλ → [gl]. The de-
    │    nasalization pass runs AFTER velar rules to avoid undoing
    │    legitimate nasal assimilation.
    │
    │  ● IPA Normalization: Replaces German uvular /ʁ/ and English
    │    approximant /ɹ/ with the alveolar trill /r/ appropriate to
    │    reconstructed Attic.
    │
    │  ● Rough Breathing & Voiceless Rho: Prepends /h/ for rough
    │    breathing (dasia) on vowel-initial words only when the IPA
    │    does not already begin with an aspirate, preventing double-
    │    aspiration artifacts. Rho with rough breathing (ῥ) is
    │    detected by walking NFD combining marks after ρ and produces
    │    either [r̥] (voiceless alveolar trill via combining ring
    │    below, U+0325) or the fallback [hr] (aspiration before
    │    trill), controlled by the voiceless_rho_combining config
    │    flag. The fallback is preferred for the German Chirp3 voice
    │    which may not support combining diacritics on consonants.
    │
    │  ● Accent Stripping: All pitch information is removed from the
    │    IPA (stress marks ˈˌ, combining accents U+0300–U+036F) so
    │    the TTS engine produces a tonally flat base. Pitch is then
    │    reintroduced exclusively through SSML <prosody contour>,
    │    giving us full control.
    │
    ▼
[4] ACCENT MAPPING — Greek-to-IPA Alignment
    │
    │  Accent type (acute, circumflex, grave) and position are detected
    │  from the NFD-decomposed Greek source text, never from IPA. The
    │  accented vowel's position is then mapped to the corresponding
    │  IPA segment through a shared vowel-unit alignment system:
    │
    │  Shared Scanner (scan_greek_vowel_units): Greek vowel units are
    │  identified, with recognized diphthongs (αι, ει, οι, αυ, ευ, ου,
    │  ηυ, υι) collapsed into single vocalic units. Diaeresis (trema,
    │  U+0308) on the second element breaks the diphthong. Each unit
    │  carries metadata: base character index, diphthong status,
    │  inherent length (η/ω, circumflex, or macron).
    │
    │  Iota subscript (U+0345, combining ypogegrammeni) is absorbed
    │  into the preceding vowel unit. In NFD, ᾳ decomposes to α +
    │  U+0345. This is NOT a separate vowel — it is a historical long
    │  diphthong element. The unit is marked as a diphthong so the
    │  unit count matches CLTK output (which renders the subscript
    │  as a semivowel [j]). No phantom vowel unit is created. The
    │  is_long flag comes from the base vowel and accent, not from
    │  the subscript itself.
    │
    │  For diphthongs, BOTH the first and second element's base
    │  indices map to the same vowel unit, because accent combining
    │  marks may attach to either element (e.g., οῦ has perispomeni
    │  on υ, not ο).
    │
    │  Shared Scanner (scan_ipa_vowel_units): IPA vowel units are
    │  identified using an explicit whitelist of IPA diphthong pairs
    │  that CLTK actually produces. The whitelist covers both vowel +
    │  vowel pairs (ai, ei, oi, au, eu, ou, yi, ɛi, ɔi) and vowel +
    │  semivowel pairs (ɑj, ej, oj, ɛj, ɔj, aj, ij, ɑw, ew, ow,
    │  ɛw, ɔw, aw) that the Probert reconstruction generates. Only
    │  whitelisted pairs are merged; all other adjacent vowels are
    │  treated as hiatus. Length markers (ː) are consumed into the
    │  preceding unit. This prevents the greedy merging of any
    │  adjacent IPA vowels that would break alignment in hiatus
    │  contexts (e.g., θέατρον → tʰeɑtron where εα is two units).
    │
    │  Alignment: The n-th Greek vowel unit maps to the n-th IPA vowel
    │  unit. Because both scanners use the same diphthong-aware logic,
    │  the unit counts stay synchronized even through diphthong-to-
    │  monophthong asymmetries, epenthesis, and contraction.
    │
    │  find_accent_in_greek returns a vowel-unit index (not a raw
    │  character index), verifies the accent sits on an actual vowel
    │  before accepting it, and breaks after the first accent found
    │  (Greek words have exactly one). If no accent is found, the word
    │  receives a flat contour — no fallback guessing.
    │
    ▼
[5] PROSODY SYNTHESIZER — Syllable-Aware Contouring
    │
    │  Pitch contours are calculated relative to the dynamic sentence
    │  baseline from [2]. Syllable boundaries are estimated from IPA
    │  vowel nuclei, and the accented syllable's proportional position
    │  within the word determines the contour timing:
    │
    │  ● Acute:      Sharp rise to peak (+35%) centered on the
    │                 accented syllable, then fall to baseline.
    │  ● Circumflex: Rise-fall (+35% → −12%) with the fall bounded
    │                 by the accented syllable's proportional duration,
    │                 preventing smear across polysyllabic words.
    │                 Monosyllable circumflexes use a tight rise-fall
    │                 (peak at 25%, fall by 65%) since there is no room
    │                 to spread. When the computed tail position reaches
    │                 100%, the redundant final contour point is omitted.
    │  ● Grave:      Suppressed rise (+5%), modeling the pitch
    │                 neutralization of non-final acutes.
    │
    │  Heavy-word rate modulation: Words containing long vowels are
    │  slowed proportionally to syllable count — monosyllables are
    │  left alone, disyllables receive half slowdown, trisyllables
    │  and above receive full slowdown — to simulate the durational
    │  weight of quantity without dragging short function words.
    │
    ▼
[6] SSML BATCHER — Prosodic Unit Assembly
    │
    │  Words are not processed in isolation. Proclitics (ὁ, εἰς, οὐκ),
    │  enclitics (τε, γε, τις), and elided forms (ἀλλ᾽, δ᾽, καθ᾿)
    │  are merged into prosodic groups before SSML generation. Elision
    │  is detected through a unified set of apostrophe-like codepoints
    │  (ELISION_MARKS) covering koronis U+1FBD, psili U+1FBF, right
    │  single quote U+2019, ASCII apostrophe U+0027, modifier letter
    │  apostrophe U+02BC, and left single quote U+2018. Each word in
    │  the group is transcribed individually by CLTK, then the IPA
    │  strings are concatenated and wrapped in a single <phoneme> tag.
    │
    │  Accent selection (select_group_accent): The group is walked in
    │  order — proclitics are skipped, elided unaccented words (e.g.,
    │  δ᾽, καθ᾿) are skipped as phonologically dependent fragments,
    │  and the first remaining accented word is the host whose accent
    │  governs the group's pitch contour. Enclitic accents are
    │  suppressed. Secondary accents on the host ultima (induced by
    │  enclitics, e.g., ἄνθρωπός τε) are detected from the source
    │  text for potential future use.
    │
    │  Breath pacing: A configurable set of conjunction and preposition
    │  triggers (καί, ἀλλά, ὅτι, etc.) insert natural breath pauses
    │  when the word count since the last pause exceeds a threshold
    │  (max_breath_words). A hard ceiling (force_breath_words) forces
    │  a pause regardless of trigger presence.
    │
    │  Pause durations are scaled by the inverse of the global speaking
    │  rate — a rate of 2.0 halves all pauses, 0.5 doubles them —
    │  keeping rhythm proportional at any speed.
    │
    │  The fragment stream is chunked into segments under a configurable
    │  byte limit (max_chunk_bytes, default 4500) to respect API limits.
    │
    ▼
[7] AUDIO RENDERER
    │
    │  Sends SSML chunks to Google Cloud TTS with exponential-backoff
    │  retry on transient errors (503, 429, 500, timeouts, resource
    │  exhaustion).
    │
    │  WAV Construction: Each API response is a complete RIFF/WAVE file.
    │  The renderer dynamically parses RIFF headers to extract the fmt
    │  subchunk (from the first successful response) and the raw PCM
    │  data payload (from every response). A clean WAV is assembled
    │  from scratch with a single RIFF header, the captured fmt chunk,
    │  and the concatenated payloads.
    │
    │  Failure Resilience: When a chunk fails after all retries, the
    │  renderer estimates the expected audio duration from the SSML
    │  content — counting syllable nuclei in phoneme tags (at ~220ms
    │  per syllable for slow formal speech) and summing explicit break
    │  durations — and inserts a correctly-sized PCM silence placeholder.
    │  This preserves temporal alignment in the output file rather than
    │  allowing words to jump forward in time. Failed chunk indices are
    │  logged in the debug output.
    │
    │  Generates an .m3u playlist for seamless playback of multi-
    │  section output.
    │
    ▼
[8] OUTPUT

    Audio files:    {output_dir}/{nn}_{slug}_{voice}_{rate}.{ext}
    Debug log:      {debug_file}  (JSON — full SSML, per-word analysis,
                                   accent mapping, downdrift values,
                                   contour strings, failure records)
    IPA cache:      transcription_cache.json
    Playlist:       {output_dir}/playlist.m3u

================================================================================
A N A L Y S I S   T O O L — analysis.py
================================================================================

A companion script generates per-section spectral analysis:

    ● 4-panel PNG per section: wideband spectrogram with F0 overlay,
      intensity contour, clean pitch track with downdrift trend line
      and accent peak markers, and word-level annotations color-coded
      by accent type (red = acute, blue = circumflex, gray = grave).

    ● Phonetic report (phonetic_report.txt): global and per-section
      statistics including F0 mean/range/std, intensity stats, F0-
      intensity Pearson correlation (low = pitch accent, high = stress
      leaking), downdrift assessment (mean F0 decline across sentence
      quarters), accent type distribution, silence region detection
      (for geminate closure measurement), and anomaly flags (F0 spikes,
      stress leakage, low voicing).

    ● Raw metrics (metrics.json): all numerical data for downstream
      processing or comparison across configuration changes.

================================================================================
K N O W N   L I M I T A T I O N S
================================================================================

    ● Zeta is rendered as [zd] per Probert/Allen reconstruction. The
      German voice may simplify this cluster. A config toggle to fall
      back to [z] is not yet implemented.

    ● Geminate consonants (λλ, ττ, μμ, ππ) appear in the IPA and
      stop geminates show longer closures spectrally, but sonorant
      geminates may not be held longer by the voice model. No rate
      slowdown wrapper for geminate segments exists yet.

    ● Interrogative intonation fails on very short sentences (1-2
      words) when the final word has accent=none. The updrift baseline
      only affects words that receive contour tags, so unaccented
      sentence-final words get no rising terminal.

    ● Enclitic-induced secondary accents on the host ultima (e.g.,
      the second acute in ἄνθρωπός τε) are detected but not rendered
      as secondary pitch peaks. The primary accent governs the group
      contour. Implementing secondary peaks would require multi-peak
      contour generation per prosodic group.

    ● number_to_greek handles 0–999. Numbers ≥ 1000 are silently
      dropped.

    ● Cache invalidation hashes the entire config.toml and script.
      Non-phonological config changes (output_dir, debug_file) will
      unnecessarily invalidate the cache.

================================================================================
C O N F I G U R A T I O N — config.toml
================================================================================

[files]
    input_text                              Source text path.
    debug_file                              Debug JSON dump path.

[options]
    dry_run                                 Bool. Skip API calls; estimate cost.
    apply_sandhi                            Bool. Merge elided words into
                                                   prosodic groups.
    apply_rough_breathing                   Bool. Pronounce the dasia as /h/.
    voiceless_rho_combining                 Bool. Use combining ring below for
                                                   voiceless rho [r̥] vs fallback
                                                   [hr]. False recommended for
                                                   Chirp3 voice.

[prosody]
    contour_peak                            Int.   Acute pitch rise (%).
    contour_grave                           Int.   Grave pitch rise (%).
    contour_end                             Int.   Post-accent pitch drop (%).
    circumflex_tail_len                     Int.   Legacy. Circumflex fall is now
                                                   bounded by syllable proportion.
    downdrift_start                         Int.   Sentence-initial baseline (%).
    downdrift_end                           Int.   Sentence-final baseline (%).
    updrift_start                           Int.   Interrogative start pitch (%).
    updrift_end                             Int.   Interrogative end pitch (%).
    heavy_word_rate                         Str.   Speed reduction for heavy words
                                                   (e.g., "-15%"). Scaled by
                                                   syllable count.
    downdrift_clause_based_rewind_scale     Float. Clause-boundary baseline reset
                                                   (0.0 = no reset, 1.0 = full
                                                   rewind to sentence start).

[pauses]
    breath, newline, comma, period, minor   Str.   Duration in ms (e.g., "145ms").
                                                   Auto-scaled by speaking_rate.

[pacing]
    force_breath_words                      Int.   Hard ceiling before forced pause.
    max_breath_words                        Int.   Soft target phrase length before
                                                   breath at next trigger word.

[processing]
    max_chunk_bytes                         Int.   Max SSML bytes per API call
                                                   (default 4500, API limit 5000).
    delimiter                               Str.   Section separator in input file.

[tts]
    voice_name                              Str.   Google voice ID.
    speaking_rate                           Float. Global speed multiplier.
    pitch                                   Float. Global pitch offset (semitones).
    audio_encoding                          Str.   "LINEAR16" (WAV) or "MP3".
    output_dir                              Str.   Output directory.

[cltk]
    dialect                                 Str.   CLTK dialect (e.g., "attic").
    reconstruction                          Str.   CLTK reconstruction (e.g.,
                                                   "probert").

[google_cloud]
    service_account_file                    Str.   Path to GCP credentials JSON.

================================================================================
D E P E N D E N C I E S
================================================================================

    cltk            Ancient Greek phonological transcription.
                    Requires 'grc_models_cltk' — the script detects missing
                    models and prints download instructions.

    google-cloud-texttospeech
                    Google Cloud TTS client. Requires a service account with
                    Text-to-Speech API enabled.

    praat-parselmouth (analysis.py only)
                    Python wrapper around Praat for pitch extraction,
                    spectrogram generation, and intensity analysis.

    matplotlib, numpy (analysis.py only)
                    Visualization and numerical computation.

================================================================================
"""


import time
import hashlib
import os
import re
import sys
import json
import struct
import unicodedata
from   xml.sax.saxutils import escape
from   cltk.phonology.grc.transcription import Transcriber
from   google.cloud                     import texttospeech

# Python 3.11+ Compatibility
try:
    import tomllib
except ImportError:
    import tomli as tomllib

# ==============================================================================
# 1. C O N F I G U R A T I O N   &   S E T U P
# ==============================================================================

if not os.path.exists("config.toml"):
    raise FileNotFoundError("CRITICAL: config.toml not found.")

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

if "google_cloud" in config:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_cloud"]["service_account_file"]

print(":: Initializing CLTK Transcriber (Attic/Probert)...")
try:
    TRANSCRIBER = Transcriber(
        dialect        = config["cltk"]["dialect"],
        reconstruction = config["cltk"]["reconstruction"]
    )
except Exception as e:
    print(f"\n[CRITICAL] CLTK Initialization Failed: {e}")
    print(":: You likely need to download the Greek models.")
    print(":: Run this python command separately:")
    print("   from cltk.data.fetch import FetchCorpus; FetchCorpus(language='grc').import_corpus('grc_models_cltk')")
    sys.exit(1)

def get_file_hash(filepath):
    if not os.path.exists(filepath): return ""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

CACHE_FILE = "transcription_cache.json"
TRANSCRIPTION_CACHE = {"_meta": {}, "words": {}}

# Calculate current state
current_config_hash = get_file_hash("config.toml")
current_script_hash = get_file_hash(__file__)

if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            loaded_cache = json.load(f)

        meta = loaded_cache.get("_meta", {})
        if (meta.get("config_hash") == current_config_hash and
            meta.get("script_hash") == current_script_hash):

            TRANSCRIPTION_CACHE = loaded_cache
            # Ensure structure exists even if loaded from older format
            if "words" not in TRANSCRIPTION_CACHE:
                TRANSCRIPTION_CACHE = {"_meta": meta, "words": {}}
            count = len(TRANSCRIPTION_CACHE.get("words", {}))
            print(f":: Cache Hit: Loaded {count} lexical entries.")
        else:
            print(":: Change detected in config or script. Invalidating cache.")
            TRANSCRIPTION_CACHE = {"_meta": {}, "words": {}}

    except Exception as e:
        print(f":: Cache Corrupted ({e}). Starting with empty lexicon.")
        TRANSCRIPTION_CACHE = {"_meta": {}, "words": {}}

# Initialize/Update metadata for the next save
TRANSCRIPTION_CACHE["_meta"] = {
    "config_hash": current_config_hash,
    "script_hash": current_script_hash
}
if "words" not in TRANSCRIPTION_CACHE:
    TRANSCRIPTION_CACHE["words"] = {}

def save_cache():
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_FILE)

# ==============================================================================
# 2. D A T A   M A P P I N G S
# ==============================================================================

BREATH_TRIGGERS = {
    "καὶ", "ἀλλὰ", "ἢ", "ὅτι", "ἵνα", "ὡς", "ὥστε", "ἐπεὶ", "ἐπειδὴ",
    "εἰς", "πρὸς", "ἐν", "ἐπὶ", "περὶ", "παρὰ", "μετὰ", "διὰ", "ὑπὲρ",
    "ἀπὸ", "ἐκ", "ἐξ", "κατὰ", "ὑπὸ", "ὃς", "ἣ", "ὃ", "οἷος", "ὅσος", "γὰρ", "δέ"
}

PROCLITICS = {
    "ὁ", "ἡ", "οἱ", "αἱ", "ἐν", "εἰς", "ἐκ", "ἐξ", "εἰ", "ὡς", "οὐ", "οὐκ", "οὐχ"
}
ENCLITICS = {
    "τε", "γε", "με", "μου", "μοι", "σε", "σου", "σοι", "τις", "τι", "που", "πως", "ποτε"
}

GREEK_NUM_BASICS = {
    0: "μηδέν",   1: "εἷς",    2: "δύο",     3: "τρεῖς",    4: "τέτταρες",
    5: "πέντε",   6: "ἕξ",     7: "ἑπτά",    8: "ὀκτώ",     9: "ἐννέα",
    10: "δέκα",   11: "ἕνδεκα", 12: "δώδεκα", 13: "τρεῖς καὶ δέκα",
    14: "τέτταρες καὶ δέκα", 15: "πεντεκαίδεκα", 16: "ἑκκαίδεκα",
    17: "ἑπτακαίδεκα", 18: "ὀκτωκαίδεκα", 19: "ἐννεακαίδεκα", 20: "εἴκοσι"
}
GREEK_TENS = {
    20: "εἴκοσι",
    30: "τριάκοντα",  40: "τεσσαράκοντα", 50: "πεντήκοντα", 60: "ἑξήκοντα",
    70: "ἑβδομήκοντα",80: "ὀγδοήκοντα",   90: "ἐνενήκοντα"
}
GREEK_HUNDREDS = {
    100: "ἑκατόν",      200: "διακόσιοι",   300: "τριακόσιοι",
    400: "τετρακόσιοι", 500: "πεντακόσιοι", 600: "ἑξακόσιοι",
    700: "ἑπτακόσιοι",  800: "ὀκτακόσιοι",  900: "ἐννακόσιοι"
}
ROMAN_MAP = {
    "i": 1,   "ii": 2,   "iii": 3,  "iv": 4,   "v": 5,
    "vi": 6,  "vii": 7,  "viii": 8, "ix": 9,   "x": 10,
    "xi": 11, "xii": 12, "xv": 15,  "xx": 20
}
LATIN_LETTERS = {
    "a": "ἄλφα", "b": "βῆτα", "c": "γάμμα", "d": "δέλτα", "e": "εἶ"
}

# IPA vowel characters used for accent detection and syllable counting.
# This covers both ASCII-range IPA and the open-mid vowels CLTK produces.
IPA_VOWELS = set("aeiouyɛɔæøəɪʊʏɑɒʌɐɤɯ")

# All codepoints that can mark elision in Greek text.
# Visually identical but different Unicode characters used by different editors.
ELISION_MARKS = {
    '\u1FBD',  # ᾽  GREEK KORONIS
    '\u1FBF',  # ᾿  GREEK PSILI (smooth breathing, reused as apostrophe)
    '\u2019',  # '  RIGHT SINGLE QUOTATION MARK
    '\u0027',  # '  APOSTROPHE (ASCII)
    '\u02BC',  # ʼ  MODIFIER LETTER APOSTROPHE
    '\u2018',  # '  LEFT SINGLE QUOTATION MARK (rare but seen)
}
def ends_with_elision(word):
    return bool(word) and word[-1] in ELISION_MARKS

# ==============================================================================
# 3. T E X T   N O R M A L I Z A T I O N
# ==============================================================================

def clean_sigla(text):
    text = re.sub(r'\{.*?\}', '', text)
    for char in ['[', ']', '<', '>', '†']:
        text = text.replace(char, '')
    return text

def number_to_greek(n):
    if n <= 20: return GREEK_NUM_BASICS.get(n, "")
    words = []
    if n >= 100:
        hundreds = (n // 100) * 100
        words.append(GREEK_HUNDREDS.get(hundreds, ""))
        n %= 100
        if n == 0: return " ".join(words)
        words.append("καὶ")
    if n >= 20:
        tens  = (n // 10) * 10
        units = n % 10
        words.append(GREEK_TENS.get(tens, ""))
        if units > 0:
            words.append("καὶ")
            words.append(GREEK_NUM_BASICS.get(units, ""))
    elif n > 0:
        words.append(GREEK_NUM_BASICS.get(n, ""))
    return " ".join([w for w in words if w])

def normalize_text_numerals(text):
    # 1. Split stuck alphanumerics ("1a" -> "1 a")
    text = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', text)

    def replace_match(match):
        token = match.group(0).lower()
        if token.isdigit():        return f" {number_to_greek(int(token))} "
        if token in ROMAN_MAP:     return f" {number_to_greek(ROMAN_MAP[token])} "
        if token in LATIN_LETTERS: return f" {LATIN_LETTERS[token]} "
        return token

    text = re.sub(r'\b([0-9]+)\b', replace_match, text)

    # Roman numeral detection with comprehensive Greek exclusion.
    #
    # The original guard used character ranges like [α-ωά-ώἀ-ῷ] which
    # have gaps in Unicode coverage. Polytonic Greek spans multiple
    # blocks and includes characters with breathing marks, iota
    # subscripts, and other diacriticals that fall outside those ranges.
    #
    # We now use the full set of relevant Unicode blocks:
    #   \u0370-\u03FF  Greek and Coptic
    #   \u1F00-\u1FFF  Greek Extended (polytonic)
    #   \u0300-\u036F  Combining Diacritical Marks (accents on any char)
    #
    # The negative lookbehind/lookahead ensures we never match a token
    # that is adjacent to ANY Greek character, even obscure ones like
    # ᾅ (U+1F85) or ῷ (U+1FF7) that the old ranges missed.

    _GREEK_ADJACENT = r'[\u0370-\u03FF\u1F00-\u1FFF\u0300-\u036Fa-zA-Z]'

    def replace_roman(match):
        token = match.group(0).lower()
        if token in ROMAN_MAP:
            return f" {number_to_greek(ROMAN_MAP[token])} "
        return match.group(0)

    # Additional safety: only match tokens that are valid Roman numerals.
    # The old regex matched any 1-4 character combination of [ivxIVX],
    # which could false-positive on strings like "vi" appearing as a
    # fragment near Greek text. We now explicitly enumerate the valid
    # Roman numeral forms from ROMAN_MAP and build an alternation.
    valid_romans = sorted(ROMAN_MAP.keys(), key=len, reverse=True)
    roman_pattern = "|".join(re.escape(r) for r in valid_romans)

    text = re.sub(
        rf'(?<!{_GREEK_ADJACENT})\b({roman_pattern})\b(?!{_GREEK_ADJACENT})',
        replace_roman,
        text,
        flags=re.IGNORECASE
    )

    def replace_latin_letter(match):
        token = match.group(0).lower()
        if token in LATIN_LETTERS:
            return f" {LATIN_LETTERS[token]} "
        return match.group(0)

    text = re.sub(
        rf'(?<=\d)[a-z]\b|\b(?<!{_GREEK_ADJACENT})[a-z]\b(?!{_GREEK_ADJACENT})',
        replace_latin_letter,
        text
    )
    return text

def romanize_greek(text):
    """
    Transliterates Greek to Latin for SSML display text (the visible content
    inside <phoneme> tags that the TTS engine never reads).

    This is INTENTIONALLY LOSSY. Accent marks and breathing marks are
    discarded. The output exists solely to satisfy the SSML parser's
    requirement for visible text content. All pronunciation is controlled
    by the IPA in the 'ph' attribute.
    """
    mapping = {
        'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z',
        'η': 'ê', 'θ': 'th','ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm',
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
        'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph','χ': 'ch','ψ': 'ps',
        'ω': 'ô'
    }

    # All recognized diphthongs get explicit romanizations.
    # Order matters: longer sequences first to prevent partial matches.
    diphthong_map = {
        'ευ': 'eu', 'αυ': 'au', 'ου': 'u',
        'αι': 'ai', 'ει': 'ei', 'οι': 'oi',
        'ηυ': 'êu', 'υι': 'yi',
    }

    apply_rough = config.get("options", {}).get("apply_rough_breathing", True)

    tokens = text.split()
    romanized_tokens = []

    for token in tokens:
        norm = unicodedata.normalize('NFD', token)
        result = []

        # Per-token rough breathing check
        if apply_rough and '\u0314' in norm:
            # Check if it's rho with dasia — don't prepend 'h' for that
            norm_lower = unicodedata.normalize('NFD', token.lower())
            chars = list(norm_lower)
            rho_has_dasia = False
            for i_ch, ch in enumerate(chars):
                if ch == 'ρ':
                    j = i_ch + 1
                    while j < len(chars) and '\u0300' <= chars[j] <= '\u036F':
                        if chars[j] == '\u0314':
                            rho_has_dasia = True
                            break
                        j += 1
                    if rho_has_dasia:
                        break

            if not rho_has_dasia:
                result.append('h')

        # Strip combining marks, producing a clean lowercase base string
        # that we can scan for diphthongs.
        base_chars = []
        for char in norm:
            if '\u0300' <= char <= '\u036F':
                continue
            base_chars.append(char.lower())

        # Walk base_chars, checking for diphthongs at each position
        i = 0
        while i < len(base_chars):
            # Try two-character diphthong match first
            if i + 1 < len(base_chars):
                pair = base_chars[i] + base_chars[i + 1]
                if pair in diphthong_map:
                    result.append(diphthong_map[pair])
                    i += 2
                    continue

            ch = base_chars[i]
            if ch in mapping:
                result.append(mapping[ch])
            elif 'a' <= ch <= 'z':
                result.append(ch)
            # else: skip (punctuation, stray characters)

            i += 1

        romanized_tokens.append("".join(result))

    return " ".join(romanized_tokens)

def make_phoneme_tag(ipa, display_text):
    """
    Builds an SSML <phoneme> tag with properly escaped content.

    The display_text is escaped for XML body context (& < >).
    The IPA string is escaped for XML attribute context (& < > "),
    preventing malformed SSML if the IPA ever contains a double
    quote, ampersand, or angle bracket.
    """
    safe_display = escape(display_text)
    safe_ipa = (
        ipa
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<phoneme alphabet="ipa" ph="{safe_ipa}">{safe_display}</phoneme>'


_RE_GREEK_CHARS = re.compile(r'[\u0370-\u03FF\u1F00-\u1FFF]')
def has_greek_chars(text):
    return bool(_RE_GREEK_CHARS.search(text))

def sanitize_filename(text):
    """
    Creates a filesystem-safe slug from Greek text.
    Only ASCII alphanumerics, hyphens, and underscores are kept.
    """
    # Transliterate Greek to Latin for the filename
    romanized = romanize_greek(text)
    romanized = re.sub(r'[\s\n\r]+', '_', romanized)
    romanized = re.sub(r'[^a-zA-Z0-9_\-]', '', romanized)
    return romanized[:50].strip('_')

# ==============================================================================
# 4. P H O N O L O G Y   &   P R O S O D Y
# ==============================================================================

def audit_ipa_diphthongs(word_list=None):
    """
    Scans CLTK transcription output for adjacent vowel pairs to discover
    which IPA diphthong combinations actually occur in practice. Run this
    against a representative word list and compare against IPA_DIPHTHONGS.

    If word_list is None, uses a built-in set of test words covering all
    Greek diphthongs in various positions (initial, medial, final) and
    potential hiatus contexts.

    Prints a report of all observed adjacent-vowel pairs and flags any
    that are NOT in the current IPA_DIPHTHONGS whitelist.
    """
    if word_list is None:
        word_list = [
            # Standard diphthongs
            "αἴξ", "αὐτός", "εἶπον", "εὑρίσκω", "οἶκος", "οὐρανός",
            "ηὗρον", "υἱός",
            # Diphthongs before vowels
            "αἰεί", "οἰόμενος", "αὐαίνω",
            # Potential hiatus (NOT diphthongs)
            "ἀοιδός", "θέατρον", "ποιέω", "βοάω", "ἐάω", "νέος",
            "ἡρωίς",  # diaeresis-like: ω + ι across morpheme boundary
            # Long vowels that might confuse
            "ψυχή", "δῶρον", "μῆνις", "τιμή",
            # Rho and breathing contexts
            "ῥήτωρ", "ῥυθμός",
            # Tricky clusters
            "πραῦς", "γεῦσις", "πνεῦμα",
            # Words with ου that map to single /uː/ vs diphthong
            "βουλή", "μοῦσα", "νοῦς",
        ]

    observed_pairs = {}  # pair_string -> list of source words
    unwhitelisted = {}

    for word in word_list:
        try:
            raw_ipa = TRANSCRIBER.transcribe(word)
        except Exception as e:
            print(f"  [!] Failed to transcribe '{word}': {e}")
            continue

        clean = unicodedata.normalize('NFD', raw_ipa)
        clean = clean.replace("[", "").replace("]", "").replace("/", "")
        clean = re.sub(r'[\u0300\u0301\u0342\u030d\u0311]', '', clean)
        clean = clean.replace('ˈ', '').replace('ˌ', '')
        clean = unicodedata.normalize('NFC', clean)
        clean = clean.replace(' ', '')

        # Scan for adjacent vowel pairs
        i = 0
        while i < len(clean) - 1:
            ch = clean[i]
            if ch.lower() in IPA_VOWELS:
                j = i + 1
                # Skip length marker
                if j < len(clean) and clean[j] == 'ː':
                    j += 1
                if j < len(clean) and clean[j].lower() in IPA_VOWELS:
                    pair = ch.lower() + clean[j].lower()
                    if pair not in observed_pairs:
                        observed_pairs[pair] = []
                    observed_pairs[pair].append((word, clean))

                    if pair not in IPA_DIPHTHONGS:
                        if pair not in unwhitelisted:
                            unwhitelisted[pair] = []
                        unwhitelisted[pair].append((word, clean))
            i += 1

    print("\n=== IPA Diphthong Audit ===")
    print(f"\nObserved adjacent vowel pairs ({len(observed_pairs)} unique):")
    for pair in sorted(observed_pairs.keys()):
        in_wl = "✓" if pair in IPA_DIPHTHONGS else "✗ NOT WHITELISTED"
        examples = ", ".join(f"{w}→{ipa}" for w, ipa in observed_pairs[pair][:3])
        print(f"  {pair}  {in_wl}  ({examples})")

    if unwhitelisted:
        print(f"\n⚠ {len(unwhitelisted)} pairs found that are NOT in IPA_DIPHTHONGS:")
        for pair in sorted(unwhitelisted.keys()):
            examples = ", ".join(f"{w}→{ipa}" for w, ipa in unwhitelisted[pair][:3])
            print(f"  {pair}  ({examples})")
        print("\nReview these. If they are true diphthongs in CLTK output,")
        print("add them to IPA_DIPHTHONGS. If they are hiatus, leave them out.")
    else:
        print("\n✓ All observed pairs are in the whitelist.")

    # Also check: are there whitelisted pairs that never appeared?
    never_seen = IPA_DIPHTHONGS - set(observed_pairs.keys())
    if never_seen:
        print(f"\nWhitelisted pairs never observed in test corpus: {never_seen}")
        print("These may be dead entries, or the test corpus needs expansion.")

    return observed_pairs, unwhitelisted

# ==============================================================================
# Shared vowel-unit scanner — used by accent mapping, quantity enforcement,
# and IPA grouping to ensure consistent diphthong handling everywhere.
# ==============================================================================

GREEK_VOWEL_CHARS = set("αεηιουωΑΕΗΙΟΥΩ")
GREEK_DIPHTHONG_SECONDS = set("ιυΙΥ")
GREEK_DIPHTHONGS = {"αι", "ει", "οι", "αυ", "ευ", "ου", "ηυ", "υι"}

IOTA_SUBSCRIPT = '\u0345'  # combining ypogegrammeni

# IPA diphthong pairs that CLTK actually produces. Only these get merged
# when scanning IPA vowel units. Anything else is treated as hiatus.
IPA_DIPHTHONGS = {
    # Vowel + vowel pairs (kept for safety)
    "ai", "ei", "oi", "au", "eu", "ou", "yi", "ɛi", "ɔi",
    # Vowel + semivowel pairs (what CLTK/Probert actually produces)
    "ɑj", "ej", "oj", "ɛj", "ɔj", "aj", "ij",  # front-closing
    "ɑw", "ew", "ow", "ɛw", "ɔw", "aw",         # back-closing
}

# Which Greek vowels are inherently long (for quantity enforcement)
INHERENTLY_LONG_VOWELS = set("ηωΗΩ")

def scan_greek_vowel_units(word):
    """
    Walks an NFD-decomposed Greek word and returns a list of vowel units.
    Each unit is a dict:
        {
            "base_idx":     int,   # index of the first vowel (counting only base chars)
            "is_diphthong": bool,
            "is_long":      bool,  # True if the base vowel is η or ω
            "char":         str,   # the base vowel character (lowercase)
        }

    Diphthongs (αι, ει, οι, αυ, ευ, ου, ηυ, υι) are collapsed into single
    units. Diaeresis (U+0308) on the second element breaks the diphthong.

    Iota subscript (U+0345, combining ypogegrammeni) is absorbed into the
    preceding vowel unit. In NFD, ᾳ decomposes to α + U+0345. This is NOT
    a separate vowel — it's a historical long diphthong element. We consume
    it as part of the combining mark stream so it never creates a phantom
    vowel unit that would break Greek-to-IPA alignment. The unit is marked
    as a diphthong (since it historically was one) but the is_long flag
    comes from the base vowel (η/ω), not from the subscript.
    """
    norm = unicodedata.normalize('NFD', word)
    chars = list(norm)
    units = []
    base_idx = -1
    i = 0

    while i < len(chars):
        char = chars[i]

        # Skip combining marks (including iota subscript in the combining range)
        if '\u0300' <= char <= '\u036F' or char == IOTA_SUBSCRIPT:
            i += 1
            continue

        base_idx += 1

        if char.lower() not in GREEK_VOWEL_CHARS:
            i += 1
            continue

        unit = {
            "base_idx":     base_idx,
            "is_diphthong": False,
            "is_long":      char.lower() in INHERENTLY_LONG_VOWELS,
            "char":         char.lower(),
        }

        # Consume combining marks after this vowel, watching for iota subscript
        j = i + 1
        has_iota_subscript = False
        has_macron = False
        while j < len(chars) and ('\u0300' <= chars[j] <= '\u036F' or chars[j] == IOTA_SUBSCRIPT):
            if chars[j] == '\u0308':
                has_diaeresis = True
            if chars[j] == '\u0304':
                has_macron = True
            if chars[j] == IOTA_SUBSCRIPT:
                has_iota_subscript = True
            j += 1

        if has_iota_subscript:
            # ᾳ, ῃ, ῳ — historically long diphthongs. Mark as diphthong
            # so the unit count matches what CLTK produces (CLTK may or
            # may not render the subscript as a vowel element — if it does,
            # this unit absorbs it; if it doesn't, we still have the correct
            # unit count because no phantom unit was created).
            unit["is_diphthong"] = True
            units.append(unit)
            i = j
            continue

        # Look ahead past combining marks for a potential diphthong second element
        # (j is already positioned past combining marks from the scan above)
        if j < len(chars) and chars[j].lower() in GREEK_DIPHTHONG_SECONDS:
            pair = char.lower() + chars[j].lower()
            if pair in GREEK_DIPHTHONGS:
                # Check for diaeresis on the second element, which breaks the diphthong
                has_diaeresis = False
                for k in range(j + 1, len(chars)):
                    if '\u0300' <= chars[k] <= '\u036F' or chars[k] == IOTA_SUBSCRIPT:
                        if chars[k] == '\u0308':
                            has_diaeresis = True
                            break
                    else:
                        break

                if not has_diaeresis:
                    unit["is_diphthong"] = True
                    units.append(unit)
                    # Advance past the second vowel and its combining marks
                    base_idx += 1
                    i = j + 1
                    while i < len(chars) and ('\u0300' <= chars[i] <= '\u036F' or chars[i] == IOTA_SUBSCRIPT):
                        i += 1
                    continue

        unit["is_long"] = char.lower() in INHERENTLY_LONG_VOWELS or has_macron

        units.append(unit)
        i = j  # skip past combining marks we already scanned

    return units

def scan_ipa_vowel_units(ipa_string):
    """
    Walks an IPA string and returns a list of vowel unit start indices.
    Consecutive vowels are merged into a single unit ONLY if they form
    a recognized IPA diphthong (from IPA_DIPHTHONGS). Semivowels j/w
    are merged when they form a recognized diphthong with the preceding
    vowel. Length markers (ː) are consumed into the preceding unit.
    Anything else is hiatus — two separate units.
    """
    IPA_DIPHTHONG_SECONDS = IPA_VOWELS | {'j', 'w'}

    units = []
    i = 0

    while i < len(ipa_string):
        ch = ipa_string[i]

        if ch.lower() not in IPA_VOWELS:
            i += 1
            continue

        unit_start = i
        i += 1

        # Consume a length marker if present
        if i < len(ipa_string) and ipa_string[i] == 'ː':
            i += 1

        # Check for a recognized diphthong: current vowel + next vowel/semivowel
        if i < len(ipa_string) and ipa_string[i].lower() in IPA_DIPHTHONG_SECONDS:
            pair = ipa_string[unit_start].lower() + ipa_string[i].lower()
            if pair in IPA_DIPHTHONGS:
                i += 1
                # Consume trailing length marker on diphthong
                if i < len(ipa_string) and ipa_string[i] == 'ː':
                    i += 1
            # else: hiatus — don't consume

        units.append(unit_start)

    return units

def find_accent_in_greek(word):
    norm = unicodedata.normalize('NFD', word)
    vowel_units = scan_greek_vowel_units(word)

    # Build a map: base_char_index -> vowel_unit_index
    # For diphthongs, BOTH the first and second element's base indices
    # must map to the same unit, because the accent combining mark may
    # attach to either element (e.g., οῦ has perispomeni on υ, not ο).
    base_idx_to_unit = {}
    for unit_idx, unit in enumerate(vowel_units):
        base_idx_to_unit[unit["base_idx"]] = unit_idx
        if unit["is_diphthong"]:
            base_idx_to_unit[unit["base_idx"] + 1] = unit_idx

    accent_type = "none"
    found_unit_idx = -1
    base_idx = -1

    for char in norm:
        if '\u0300' <= char <= '\u036F' or char == IOTA_SUBSCRIPT:
            if char == '\u0342':
                candidate = "circumflex"
            elif char == '\u0301':
                candidate = "acute"
            elif char == '\u0300':
                candidate = "grave"
            else:
                continue

            if base_idx in base_idx_to_unit:
                accent_type = candidate
                found_unit_idx = base_idx_to_unit[base_idx]
                break
            continue

        base_idx += 1

    return accent_type, found_unit_idx

def map_greek_vowel_unit_to_ipa(word, greek_vowel_unit_idx, ipa_string):
    """
    Given the index of the accented vowel UNIT (from find_accent_in_greek),
    returns the character index in ipa_string where that unit starts.

    Both sides use the shared vowel-unit scanners so diphthong counting
    is always consistent.
    """
    if greek_vowel_unit_idx < 0:
        return -1

    ipa_units = scan_ipa_vowel_units(ipa_string)

    if greek_vowel_unit_idx < len(ipa_units):
        return ipa_units[greek_vowel_unit_idx]

    # Fallback: more Greek units than IPA units — return the last IPA unit
    if ipa_units:
        return ipa_units[-1]

    return -1

def _enforce_quantity_from_source(greek_word, ipa_string):
    """
    Walks Greek vowel units and IPA vowel units in parallel. If a Greek
    unit is inherently long (η, ω) and the corresponding IPA unit lacks
    a length marker (ː), one is inserted after the IPA vowel.

    Diphthongs with a short first element (αι, ει, etc.) are skipped —
    their length is positional, not inherent, and CLTK handles them.
    Diphthongs with an inherently long first element (ηυ) DO get length
    enforcement, preserving the distinction from short-first diphthongs.

    Uses the shared vowel-unit scanners so diphthong alignment matches
    exactly.
    """
    greek_units = scan_greek_vowel_units(greek_word)
    ipa_units   = scan_ipa_vowel_units(ipa_string)

    def ipa_unit_already_long(unit_start_idx):
        j = unit_start_idx + 1
        while j < len(ipa_string):
            ch = ipa_string[j]
            if ch == 'ː':
                return True
            if ch.lower() in IPA_VOWELS:
                j += 1
                continue
            break
        return False

    def ipa_unit_end(unit_idx):
        """
        Returns the index ONE PAST the last character of this IPA vowel unit.
        """
        start = ipa_units[unit_idx]
        j = start + 1
        while j < len(ipa_string):
            ch = ipa_string[j]
            if ch == 'ː' or ch.lower() in IPA_VOWELS:
                j += 1
            else:
                break
        return j

    insertions = []
    count = min(len(greek_units), len(ipa_units))
    for v_idx in range(count):
        gu = greek_units[v_idx]

        # Skip units that aren't inherently long
        if not gu["is_long"]:
            continue

        # For diphthongs: only enforce if the first element is inherently long.
        # is_long is set from the first vowel character, so ηυ has is_long=True
        # and αυ has is_long=False. This check is already correct — but we
        # need to NOT skip diphthongs entirely when they are long.

        if ipa_unit_already_long(ipa_units[v_idx]):
            continue

        # Insert ː right after the first vowel of the IPA unit (before
        # the diphthong's second element, if present). For monophthongs
        # this goes after the vowel. For ηυ → eːu rather than euː.
        if gu["is_diphthong"]:
            # Length marker goes after the first vowel character only
            insert_pos = ipa_units[v_idx] + 1
        else:
            insert_pos = ipa_unit_end(v_idx)

        insertions.append(insert_pos)

    ipa_list = list(ipa_string)
    for pos in reversed(insertions):
        ipa_list.insert(pos, 'ː')

    return "".join(ipa_list)

def _apply_gamma_nasalization(ipa):
    """
    Enforces velar nasal [ŋ] before velars and the nasal clusters.
    Also corrects CLTK's over-nasalization of γ before non-velars
    (γν, γμ, γλ) back to [g].
    """
    for g in ['g', 'ɡ']:
        ipa = ipa.replace(f'{g}ks', 'ŋks')
        ipa = ipa.replace(f'{g}ξ', 'ŋks')
        ipa = ipa.replace(f'{g}{g}', f'ŋ{g}')
        ipa = ipa.replace(f'{g}k', 'ŋk')
        ipa = ipa.replace(f'{g}x', 'ŋx')
        ipa = ipa.replace(f'{g}χ', 'ŋx')

    # CLTK over-nasalizes γ before non-velar consonants.
    # γν → [gn], γμ → [gm], γλ → [gl] — not nasal assimilation contexts.
    # Must run AFTER the velar rules above so we don't undo legitimate ŋ.
    ipa = ipa.replace('ŋn', 'gn')
    ipa = ipa.replace('ŋm', 'gm')
    ipa = ipa.replace('ŋl', 'gl')

    return ipa

def _apply_rough_breathing(greek_word, ipa):
    """
    Handles rough breathing (dasia) on vowels and on rho.

    - Vowel-initial words with dasia: prepend [h] if not already aspirated.
    - ῥ (rho with dasia): produce [r̥] (voiceless alveolar trill).
      Falls back to [hr] if the voice can't handle combining diacritics,
      which at least produces audible aspiration before the trill.
    """
    norm = unicodedata.normalize('NFD', greek_word)
    apply_rough = config.get("options", {}).get("apply_rough_breathing", True)

    if not apply_rough:
        return ipa

    if '\u0314' not in norm:  # No dasia present
        return ipa

    # Check if this is rho with rough breathing
    # ῥ in NFD is: ρ + combining reversed comma above (U+0314)
    # It can appear word-initially (ῥήτωρ) or as ῤῥ medially.
    lower = greek_word.lower()
    nfd_lower = unicodedata.normalize('NFD', lower)

    # Find if ρ carries the dasia
    rho_has_dasia = False
    chars = list(nfd_lower)
    for i_ch, ch in enumerate(chars):
        if ch == 'ρ':
            # Check if the combining marks following this ρ include U+0314
            j = i_ch + 1
            while j < len(chars) and '\u0300' <= chars[j] <= '\u036F':
                if chars[j] == '\u0314':
                    rho_has_dasia = True
                    break
                j += 1
            if rho_has_dasia:
                break

    if rho_has_dasia:
        # Try voiceless trill r̥ (r + combining ring below U+0325).
        # The German Chirp3 voice may not support combining diacritics
        # on consonants. We use a config flag to select the fallback.
        use_combining_voiceless = config.get("options", {}).get("voiceless_rho_combining", False)
        idx = ipa.find('r')
        if idx >= 0:
            if use_combining_voiceless:
                ipa = ipa[:idx] + 'r\u0325' + ipa[idx+1:]
            else:
                # Fallback: [hr] — aspiration before trill. Not phonetically
                # identical to a voiceless trill, but audibly distinct from
                # plain [r] and within the German voice's capabilities.
                ipa = ipa[:idx] + 'hr' + ipa[idx+1:]
        return ipa

    # Vowel-initial word with dasia: prepend [h]
    if not (ipa.startswith('h') or ipa.startswith('ʰ')):
        ipa = 'h' + ipa

    return ipa


def select_group_accent(group_words):
    """
    Given a list of words forming a prosodic group (proclitics + host + enclitics),
    determines the accent type and IPA index for the group's combined IPA string.

    Rules:
    - Proclitics are unaccented; skip them.
    - The host word's accent is primary and governs the group contour.
    - Enclitic accents are suppressed for contour purposes.

    NOTE: Enclitic-induced secondary accents on the host ultima (e.g., the
    second acute in ἄνθρωπός τε) are NOT currently extracted. The primary
    accent (antepenult) is used for the contour. Implementing secondary
    pitch bumps would require find_accent_in_greek to return ALL accents
    rather than stopping at the first, plus contour generation that supports
    multiple peaks per prosodic group. This is a known simplification.

    Returns (accent_type, accent_ipa_idx, combined_ipa)
    """
    group_ipa_parts = []
    group_accent_type = "none"
    group_accent_ipa_idx = -1
    running_ipa_len = 0

    word_data_list = []
    for gw in group_words:
        if not has_greek_chars(gw):
            word_data_list.append(None)
            continue
        w_data = analyze_word_data(gw)
        word_data_list.append(w_data)
        if w_data:
            group_ipa_parts.append(w_data["ipa"])
        else:
            group_ipa_parts.append("")

    combined_ipa = "".join(group_ipa_parts)

    if not combined_ipa:
        return "none", -1, ""

    running_ipa_len = 0
    host_found = False

    for i_gw, gw in enumerate(group_words):
        w_data = word_data_list[i_gw]
        if w_data is None:
            continue

        is_proclitic = gw.lower() in PROCLITICS

        # Elided words (δ᾽, ἀλλ᾽, καθ᾽, etc.) without an accent
        # are not hosts — they are phonologically dependent fragments.
        # Skip them like proclitics so the next accented word governs.
        is_elided_unaccented = (
            w_data["accent_type"] == "none" and
            ends_with_elision(gw)
        )

        if not host_found and not is_proclitic and not is_elided_unaccented:
            host_found = True

            if w_data["accent_type"] != "none" and w_data["accent_idx"] >= 0:
                group_accent_type = w_data["accent_type"]
                group_accent_ipa_idx = running_ipa_len + w_data["accent_idx"]

        running_ipa_len += len(w_data["ipa"])

    return group_accent_type, group_accent_ipa_idx, combined_ipa


# In analyze_word_data, we track how many new entries have been added
# since the last save, and flush periodically.

class _CacheFlushTracker:
    def __init__(self, interval=50):
        self.dirty_count = 0
        self.interval = interval

    def mark_dirty(self):
        self.dirty_count += 1
        if self.dirty_count >= self.interval:
            save_cache()
            self.dirty_count = 0

_cache_tracker = _CacheFlushTracker()

def analyze_word_data(word):
    """
    Philological analysis of a single Greek word.
    1. Transcribes to IPA via CLTK.
    2. Applies gamma nasalization.
    3. Normalizes r-sounds to alveolar trill.
    4. Enforces vowel quantity from Greek source characters.
    5. Handles rough breathing (including voiceless rho).
    6. Strips IPA pitch accents for flat TTS base.
    7. Detects accent from Greek source and maps to IPA position.

    Periodically flushes the cache to disk so a crash mid-section
    doesn't lose all new transcriptions.
    """
    if not word.strip():
        return None

    cache = TRANSCRIPTION_CACHE["words"]
    if word in cache:
        return cache[word]

    try:
        raw_ipa  = TRANSCRIBER.transcribe(word)
        norm_ipa = unicodedata.normalize('NFD', raw_ipa)

        # 1. Detect accent from GREEK SOURCE (not IPA)
        accent_type, accent_vowel_unit = find_accent_in_greek(word)

        # 2. Clean IPA for audio generation
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—\']', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "")

        # 3. Strip accents from IPA
        clean_ipa = re.sub(r'[\u0300-\u036F]', '', clean_ipa)
        clean_ipa = clean_ipa.replace('ˈ', '').replace('ˌ', '')
        clean_ipa = unicodedata.normalize('NFC', clean_ipa)

        # 4. Gamma nasalization
        corrections = []

        before = clean_ipa
        clean_ipa = _apply_gamma_nasalization(clean_ipa)
        if clean_ipa != before: corrections.append("gamma_nasal")

        # 5. IPA normalization (trilled R)
        before = clean_ipa
        clean_ipa = clean_ipa.replace('ʁ', 'r').replace('ɹ', 'r')
        if clean_ipa != before: corrections.append("r_normalization")

        # 6. Quantity enforcement
        before = clean_ipa
        clean_ipa = _enforce_quantity_from_source(word, clean_ipa)
        if clean_ipa != before: corrections.append("quantity_enforcement")

        # 7. Rough breathing
        before = clean_ipa
        clean_ipa = _apply_rough_breathing(word, clean_ipa)
        if clean_ipa != before: corrections.append("rough_breathing")

        # 8. Map accent from Greek vowel-unit index to IPA character index
        accent_idx = -1
        if accent_type != "none" and accent_vowel_unit >= 0:
            accent_idx = map_greek_vowel_unit_to_ipa(word, accent_vowel_unit, clean_ipa)

        greek_unit_count = len(scan_greek_vowel_units(word))
        ipa_unit_count   = len(scan_ipa_vowel_units(clean_ipa))

        long_markers    = clean_ipa.count('ː')
        has_long_vowels = bool(re.search(r'[ηω]', word))
        is_heavy        = long_markers > 0 or has_long_vowels

        data = {
            "raw_ipa":          raw_ipa,
            "ipa":              clean_ipa,
            "accent_type":      accent_type,
            "accent_idx":       accent_idx,
            "accent_unit":      accent_vowel_unit,
            "greek_vowel_units": greek_unit_count,
            "ipa_vowel_units":  ipa_unit_count,
            "corrections":      corrections,
            "is_heavy":         is_heavy,
            "len":              len(clean_ipa)
        }

        if greek_unit_count != ipa_unit_count:
            print(f"    [!] Vowel unit mismatch: '{word}' greek={greek_unit_count} ipa={ipa_unit_count} raw={raw_ipa}")

        cache[word] = data
        _cache_tracker.mark_dirty()
        return data

    except Exception as e:
        print(f"    [!] IPA Transcription failed for '{word}': {e}")
        return None

def calculate_prosody(word_data, baseline_shift=0):
    """
    Calculates SSML pitch contour and rate for a prosodic unit.

    The contour positions are calculated relative to the accented SYLLABLE's
    proportion of the word, not just the raw character index. This ensures
    that circumflex rise-fall timing is anchored to the syllable boundary
    rather than spread across the entire word duration.
    """
    if not word_data: return None, "0%"

    ipa    = word_data["ipa"]
    a_type = word_data["accent_type"]
    idx    = word_data["accent_idx"]
    total  = word_data["len"]

    c_peak  = config["prosody"].get("contour_peak",  35)
    c_grave = config["prosody"].get("contour_grave", 5)
    c_end   = config["prosody"].get("contour_end",   -12)

    val_start = baseline_shift
    val_peak  = baseline_shift + c_peak
    val_grave = baseline_shift + c_grave
    val_end   = baseline_shift + c_end

    def p(val): return f"{int(val):+d}%"

    # --- Syllable-Aware Position Calculation ---
    # Instead of using raw character index / total length, we estimate
    # syllable boundaries by finding vowel nuclei in the IPA. The accent
    # position is then expressed as "which syllable out of how many",
    # giving a much more stable timing anchor.
    syllable_starts = []
    in_vowel = False
    for i_ch, ch in enumerate(ipa):
        if ch.lower() in IPA_VOWELS:
            if not in_vowel:
                syllable_starts.append(i_ch)
                in_vowel = True
        elif ch != 'ː':
            in_vowel = False

    num_syllables = len(syllable_starts)

    # Find which syllable the accent falls on
    accent_syllable = 0
    if idx >= 0 and syllable_starts:
        for s_idx, s_start in enumerate(syllable_starts):
            if s_start <= idx:
                accent_syllable = s_idx
            else:
                break

    # Calculate the temporal position of the accented syllable
    # as a percentage of the word's duration. Each syllable is
    # assumed to occupy roughly equal time (a simplification, but
    # far better than raw character position).
    if num_syllables > 1:
        # Center of the accented syllable
        syllable_center = (accent_syllable + 0.5) / num_syllables
        peak_pct = int(syllable_center * 100)
        peak_pct = max(5, min(95, peak_pct))
    elif num_syllables == 1:
        peak_pct = 40  # Monosyllable: peak slightly before center
    else:
        peak_pct = 50

    # Calculate syllable duration as a percentage of the word
    # Used for circumflex tail: the fall should complete within
    # the accented syllable, not spill across the whole word.
    if num_syllables > 0:
        syllable_duration_pct = int(100 / num_syllables)
    else:
        syllable_duration_pct = 100

    # --- Contour Calculation ---
    contour = None
    if idx >= 0 and total > 0:

        if a_type == "circumflex":
            if num_syllables <= 1:
                # Monosyllable circumflex: tight rise-fall within the single vowel.
                # Peak early, fall by ~65% of the word. No room to spread.
                contour = (
                    f"(0%,{p(val_start)}) "
                    f"(25%,{p(val_peak)}) "
                    f"(65%,{p(val_end)}) "
                    f"(100%,{p(val_end)})"
                )
            else:
                # Polysyllabic: fall bounded by the accented syllable's duration.
                tail_pct = min(peak_pct + max(syllable_duration_pct // 2, 8), 100)
                contour = (
                    f"(0%,{p(val_start)}) "
                    f"({peak_pct}%,{p(val_peak)}) "
                    f"({tail_pct}%,{p(val_end)}) "
                    f"(100%,{p(val_end)})"
                )

        elif a_type == "grave":
            contour = (
                f"(0%,{p(val_start)}) "
                f"({peak_pct}%,{p(val_grave)}) "
                f"(100%,{p(val_end)})"
            )

        else:  # acute
            contour = (
                f"(0%,{p(val_start)}) "
                f"({peak_pct}%,{p(val_peak)}) "
                f"(100%,{p(val_end)})"
            )

    # --- "Heavy Word" Smoothing ---
    rate = "0%"
    if word_data["is_heavy"]:
        base_slowdown = int(config["prosody"].get("heavy_word_rate", "-15%").strip('%'))

        # Count vowel nuclei (syllables), not just vowel characters
        vowel_count = num_syllables

        if vowel_count < 2:
            rate = "0%"
        elif vowel_count == 2:
            rate = f"{int(base_slowdown / 2)}%"
        else:
            rate = f"{base_slowdown}%"

    return contour, rate

def is_breath_trigger(word):
    w = unicodedata.normalize('NFC', word.lower())
    return w in BREATH_TRIGGERS

# ==============================================================================
# 5. S S M L   C O N S T R U C T I O N
# ==============================================================================

# The Greek question mark (;) is U+037E. It looks identical to ASCII semicolon
# (U+003B) but is a different codepoint. Many Greek texts use one or the other
# inconsistently. We normalize U+037E to U+003B early so that sentence
# splitting and interrogative detection work regardless of which codepoint
# the source text uses.
#
# Similarly, the Greek ano teleia (·) is U+0387, which is visually identical
# to middle dot (U+00B7). Normalize both to a consistent form.

def normalize_greek_punctuation(text):
    """
    Normalizes Greek-specific punctuation codepoints to their ASCII
    equivalents so downstream regex patterns don't need to match both.

    U+037E (Greek question mark)  → U+003B (semicolon)
    U+0387 (Greek ano teleia)     → U+00B7 (middle dot) — we keep · for
                                    clause boundary detection, just ensure
                                    it's the consistent codepoint.
    """
    text = text.replace('\u037E', ';')   # Greek question mark → ASCII semicolon
    text = text.replace('\u0387', '·')   # Greek ano teleia → middle dot (U+00B7)
    return text

def scale_time(time_str, rate):
    """
    Scales a time duration string (e.g., "145ms") by the inverse of the
    speaking rate. A rate of 2.0 halves all pauses; a rate of 0.5 doubles them.

    Returns the original string unchanged if it can't be parsed, but logs
    a warning so malformed config values don't silently produce wrong timing.
    """
    if not isinstance(time_str, str) or not time_str.endswith("ms"):
        return time_str

    digits = time_str[:-2].strip()
    if not digits.isdigit():
        print(f"    [!] Warning: Could not parse pause duration '{time_str}'. Using as-is.")
        return time_str

    val = int(digits)
    if rate <= 0:
        print(f"    [!] Warning: speaking_rate is {rate}, which is invalid. Not scaling pauses.")
        return time_str

    return f"{int(val / rate)}ms"

def build_ssml_fragments(full_text):

     # 0. Normalize Greek punctuation codepoints BEFORE anything else
    full_text = normalize_greek_punctuation(full_text)

    # 1. Cleaning
    full_text = clean_sigla(normalize_text_numerals(full_text))
    full_text = full_text.replace("\r\n", "\n")

    # 2. Tokenize Paragraphs
    token_dbl = "||DBL_BRK||"
    full_text = re.sub(r'\n\s*\n+', token_dbl, full_text)
    full_text = full_text.replace("\n", " ")

    # 3. Config
    rate_global  = config["tts"].get("speaking_rate", 1.0)
    pauses       = config.get("pauses", {})
    pacing       = config.get("pacing", {})

    # --- Tunable Intonation ---
    drift_start   = config["prosody"].get("downdrift_start", 10)
    drift_end     = config["prosody"].get("downdrift_end", -10)
    updrift_start = config["prosody"].get("updrift_start", -5)
    updrift_end   = config["prosody"].get("updrift_end", 10)

    rewind_scale = config["prosody"].get("downdrift_clause_based_rewind_scale", 0.3)
    apply_sandhi = config.get("options", {}).get("apply_sandhi", True)

    t_breath     = scale_time(pauses.get("breath",  "145ms"), rate_global)
    t_newline    = scale_time(pauses.get("newline", "180ms"), rate_global)
    t_comma      = scale_time(pauses.get("comma",   "80ms"), rate_global)
    t_period     = scale_time(pauses.get("period",  "145ms"), rate_global)
    t_minor      = scale_time(pauses.get("minor",   "215ms"), rate_global)

    max_breath   = pacing.get("max_breath_words", 9)
    force_breath = pacing.get("force_breath_words", 20)

    fragments     = []
    debug_entries = []

    # 4. Split Sentences (Preserving Delimiters)
    sentence_pattern = r'([.;]|\|\|DBL_BRK\|\|)'
    raw_sentences    = re.split(sentence_pattern, full_text)

    sentences = []
    for i in range(0, len(raw_sentences)-1, 2):
        sentences.append(raw_sentences[i] + raw_sentences[i+1])
    if len(raw_sentences) % 2 != 0 and raw_sentences[-1]:
        sentences.append(raw_sentences[-1])

    # 5. Process
    for sentence in sentences:

        # Analyze for Downdrift
        clean_words_in_sentence = [w for w in sentence.split() if has_greek_chars(w)]
        total_sentence_words    = len(clean_words_in_sentence)
        current_word_idx        = 0

        # Interrogative Intonation (Tunable Updrift)
        current_drift_start = drift_start
        current_drift_end   = drift_end
        if sentence.strip().endswith(';'):
            current_drift_start = updrift_start
            current_drift_end   = updrift_end

        part_pattern = r'([,·:\-]|\.|\|\|DBL_BRK\|\|)'
        parts        = re.split(part_pattern, sentence)

        words_since_breath = 0

        for raw_part in parts:

            # Handle Paragraph Breaks
            if raw_part == token_dbl:
                fragments.append(f'<break time="{t_newline}"/>')
                debug_entries.append({"type": "break", "kind": "newline"})
                continue

            part = raw_part.strip()
            if not part: continue

            if part in [',', ':', '.', ';', '—', '·', '-']:
                t = t_period
                if   part == ',':              t = t_comma
                elif part in ['·', '-', ':']:  t = t_minor
                fragments.append(f'<break time="{t}"/>')
                words_since_breath = 0

                # Clause-Based Intonation Reset
                if total_sentence_words > 0:
                    rewind_amount = int(total_sentence_words * rewind_scale)
                    current_word_idx = max(0, current_word_idx - rewind_amount)

                continue

            words = part.split()

            # Enclitic / Proclitic / Sandhi Grouping
            # Instead of fusing words into a single string (which confuses CLTK),
            # we group them into prosodic units. Each word is transcribed
            # individually and the IPA is concatenated.
            i = 0
            while i < len(words):
                word = words[i]

                # Collect a prosodic group: the head word plus any
                # proclitics before it, enclitics after it, and elisions.
                group = [word]
                merged = True

                while merged and i + 1 < len(words):
                    merged = False
                    next_word = words[i+1]
                    if not has_greek_chars(next_word): break

                    if apply_sandhi and ends_with_elision(group[-1]):
                        i += 1
                        group.append(words[i])
                        merged = True
                        continue

                    if group[0].lower() in PROCLITICS and len(group) == 1:
                        i += 1
                        group.append(words[i])
                        merged = True
                        continue

                    if next_word.lower() in ENCLITICS:
                        i += 1
                        group.append(words[i])
                        merged = True
                        continue

                if not any(has_greek_chars(w) for w in group):
                    joined = " ".join(group)
                    if joined.strip(): fragments.append(escape(joined))
                    i += 1
                    continue

                words_since_breath += len(group)
                if words_since_breath > max_breath:
                    if is_breath_trigger(group[0]) or words_since_breath >= force_breath:
                        fragments.append(f'<break time="{t_breath}"/>')
                        words_since_breath = 0

                # Downdrift
                if total_sentence_words > 1:
                    position_ratio = current_word_idx / (total_sentence_words - 1)
                else:
                    position_ratio = 0.0

                current_baseline  = current_drift_start + ((current_drift_end - current_drift_start) * position_ratio)
                current_word_idx += len(group)

                # Phonology: use shared accent selection for the prosodic group
                group_accent_type, group_accent_ipa_idx, combined_ipa = select_group_accent(group)

                if not combined_ipa:
                    dummy_text = romanize_greek(" ".join(group))
                    if dummy_text.strip():
                        fragments.append(escape(dummy_text))
                    i += 1
                    continue

                # Check heaviness across the group
                is_heavy = any(
                    (TRANSCRIPTION_CACHE["words"].get(gw, {}).get("is_heavy", False))
                    for gw in group if has_greek_chars(gw)
                )

                # Build a synthetic word_data for the prosodic group
                group_word_data = {
                    "ipa":         combined_ipa,
                    "accent_type": group_accent_type,
                    "accent_idx":  group_accent_ipa_idx,
                    "is_heavy":    is_heavy,
                    "len":         len(combined_ipa)
                }

                dummy_text = romanize_greek(" ".join(group))
                contour, dur_rate = calculate_prosody(group_word_data, baseline_shift=current_baseline)

                final_ssml = make_phoneme_tag(combined_ipa, dummy_text)

                if dur_rate != "0%":
                    final_ssml = f'<prosody rate="{dur_rate}">{final_ssml}</prosody>'
                if contour:
                    final_ssml = f'<prosody contour="{contour}">{final_ssml}</prosody>'

                fragments.append(final_ssml)

                debug_entries.append({
                    "greek":     " ".join(group),
                    "roman":     dummy_text,
                    "ipa":       combined_ipa,
                    "accent":    group_accent_type,
                    "downdrift": int(current_baseline),
                    "contour":   contour
                })

                if i < len(words) - 1:
                    fragments.append(" ")

                i += 1

    return fragments, debug_entries

# ==============================================================================
# 6. A U D I O  R E N D E R E R
# ==============================================================================

def parse_wav_fmt(wav_bytes):
    """
    Parses a RIFF/WAVE file and extracts the fmt chunk parameters and
    the raw audio payload from the data chunk.
    Returns (fmt_bytes, audio_payload) or (None, None) on failure.
    fmt_bytes is the complete fmt subchunk (id + size + body).
    """
    if len(wav_bytes) < 12:
        return None, None

    if wav_bytes[0:4] != b'RIFF' or wav_bytes[8:12] != b'WAVE':
        return None, None

    pos = 12
    length = len(wav_bytes)
    fmt_chunk = None
    data_payload = None

    while pos + 8 <= length:
        chunk_id = wav_bytes[pos:pos+4]
        try:
            chunk_size = struct.unpack('<I', wav_bytes[pos+4:pos+8])[0]
        except struct.error:
            break

        chunk_body = wav_bytes[pos+8:pos+8+chunk_size]

        if chunk_id == b'fmt ':
            # Store the entire subchunk: id + size + body
            fmt_chunk = wav_bytes[pos:pos+8+chunk_size]
        elif chunk_id == b'data':
            data_payload = chunk_body

        pos += 8 + chunk_size
        # WAV chunks must be word-aligned
        if chunk_size % 2 == 1:
            pos += 1

    return fmt_chunk, data_payload

def build_wav_file(fmt_chunk, all_payloads):
    """
    Constructs a complete, valid RIFF/WAVE file from a fmt subchunk
    and a list of raw PCM audio payloads.
    """
    combined_data = b''.join(all_payloads)
    data_chunk = b'data' + struct.pack('<I', len(combined_data)) + combined_data

    wave_body = fmt_chunk + data_chunk
    riff_header = b'RIFF' + struct.pack('<I', 4 + len(wave_body)) + b'WAVE'

    return riff_header + wave_body

def extract_wav_payload(wav_bytes):
    """
    Robustly parses RIFF/WAVE structure to find the 'data' chunk.
    This prevents corruption if Google adds metadata headers.
    """
    _, payload = parse_wav_fmt(wav_bytes)
    if payload is not None:
        return payload

    # Fallback: If parsing fails, assume standard header size (44 bytes)
    if len(wav_bytes) > 44:
        return wav_bytes[44:]
    return b""

def fetch_audio_bytes(client, ssml_chunk, voice_params, audio_config, max_retries=3):
    """
    Sends an SSML chunk to Google Cloud TTS with retry logic.
    Retries on transient errors with exponential backoff.
    """
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml_chunk)

    for attempt in range(1, max_retries + 1):
        try:
            response = client.synthesize_speech(
                request=texttospeech.SynthesizeSpeechRequest(
                    input=synthesis_input, voice=voice_params, audio_config=audio_config
                )
            )
            return response.audio_content
        except Exception as e:
            error_str = str(e).lower()
            # Determine if this is a retryable error
            is_transient = any(keyword in error_str for keyword in [
                "unavailable", "deadline", "timeout", "503", "500",
                "internal", "resource_exhausted", "429"
            ])

            if is_transient and attempt < max_retries:
                wait = 2 ** attempt
                print(f"    -> API Error (attempt {attempt}/{max_retries}): {e}")
                print(f"    -> Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    -> API Error (FATAL after {attempt} attempts): {e}")
                return None

    return None

def load_sections(input_path: str, delimiter: str, section_filter: list[int] | None = None) -> list[tuple[int, str]]:
    """
    Load sections from a text file, split by '---' delimiters.
    Lines starting with '#' within a section are stripped as comments.
    Returns list of (section_number, text) tuples.
    section_filter: if non-empty, only return sections whose 1-based index is in the list.
    """
    sections = []
    with open(input_path, "r", encoding="utf-8") as f: 
        content = f.read()
        blocks  = content.split(delimiter)
        for i, block in enumerate(blocks):
            # Strip comment lines and blank lines
            lines = []
            for line in block.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    lines.append(stripped)
            text = " ".join(lines).strip()
            if not text:
                continue
            section_num = len(sections) + 1
            sections.append((section_num, text))
        
        if section_filter:
            sections = [(num, text) for num, text in sections if num in section_filter]
    
    return sections

def generate_audio():
    # Load Cdef generate_audio():
    input_path     = config["files"].get("input_text", "input.txt")
    output_dir     = config["files"].get("output_dir", "output")
    debug_path     = config["files"].get("debug_file", "debug_dump.json")

    # Load Config
    voice_name = config["tts"].get("voice_name", "de-DE-Standard-E")
    rate       = config["tts"].get("speaking_rate", 1.0)
    audio_enc  = config["tts"].get("audio_encoding", "LINEAR16")
    pitch_val  = config["tts"].get("pitch", 0.0)
    max_bytes  = config["processing"].get("max_chunk_bytes", 4500)
    delimiter  = config["processing"].get("delimiter", "---")
    dry_run    = config["options"].get("dry_run", False)

    section_filter = config["processing"].get("sections_to_generate", [])

    ext = "wav" if audio_enc == "LINEAR16" else "mp3"

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return
    sections = load_sections(input_path, delimiter, section_filter)

    print(f":: Processing {len(sections)} sections...")
    if dry_run: print(":: DRY RUN MODE: No audio will be generated.")

    client = None
    os.makedirs(output_dir, exist_ok=True)
    if not dry_run:
        client = texttospeech.TextToSpeechClient()

    # --- Dynamic Voice Configuration ---
    # Handle language code extraction for all voice types (Standard, Chirp, Studio)
    parts = voice_name.split("-")
    if len(parts) >= 2:
        lang_code = f"{parts[0]}-{parts[1]}"
    else:
        lang_code = "de-DE"

    voice_params = texttospeech.VoiceSelectionParams(
        language_code=lang_code, 
        name=voice_name
    )

    encoding_enum = texttospeech.AudioEncoding.LINEAR16
    if audio_enc == "MP3": encoding_enum = texttospeech.AudioEncoding.MP3

    audio_cfg = texttospeech.AudioConfig(
        audio_encoding=encoding_enum, 
        speaking_rate=rate, 
        pitch=pitch_val
    )

    full_debug_log = []
    generated_files = []
    total_chars = 0

    for section_num, text in sections:
        total_chars += len(text)
        print(f":: Generating Section {section_num}...")
        fragments, section_debug = build_ssml_fragments(text)

        full_ssml_string = "".join(fragments)

        if dry_run:
            print(f"    [Dry Run] Section {section_num} processed.")
            full_debug_log.append({
                "section": section_num,
                "mode": "dry_run",
                "ssml": full_ssml_string,
                "analysis": section_debug
            })
            continue

        # --- Audio Generation Variables ---
        current_ssml_parts = ["<speak>"]
        current_length     = len("<speak>")

        fmt_chunk      = None
        audio_payloads = []
        mp3_buffer     = bytearray()

        chunk_count    = 0
        failed_chunks  = []

        # --- Helper: Silence Generator ---
        def generate_silence_payload(duration_ms, sample_rate=24000, sample_width=2):
            num_samples = int(sample_rate * duration_ms / 1000)
            return b'\x00' * (num_samples * sample_width)

        # --- Helper: Chunk Duration Estimator ---
        def estimate_chunk_duration_ms(ssml_parts):
            text = "".join(ssml_parts)
            duration = 0
            for match in re.finditer(r'<break\s+time="(\d+)ms"\s*/>', text):
                duration += int(match.group(1))
            
            # Estimate word/syllable duration (~220ms per syllable)
            ms_per_syllable = 220
            total_syllables = 0
            for match in re.finditer(r'<phoneme[^>]*ph="([^"]*)"', text):
                ipa = match.group(1)
                in_vowel = False
                for ch in ipa:
                    if ch.lower() in IPA_VOWELS:
                        if not in_vowel:
                            total_syllables += 1
                            in_vowel = True
                    elif ch != 'ː':
                        in_vowel = False
            
            phoneme_count = len(re.findall(r'<phoneme', text))
            if total_syllables < phoneme_count:
                total_syllables = phoneme_count
            
            duration += total_syllables * ms_per_syllable
            return max(duration, 200)

        # --- Helper: Flush Buffer to API ---
        def flush_buffer(parts):
            nonlocal fmt_chunk, chunk_count
            parts_for_send = list(parts)
            parts_for_send.append("</speak>")
            ssml_string = "".join(parts_for_send)
            
            # API CALL
            chunk_bytes = fetch_audio_bytes(client, ssml_string, voice_params, audio_cfg)

            chunk_count += 1

            if chunk_bytes is None:
                est_ms = estimate_chunk_duration_ms(parts_for_send)
                print(f"    [!] Chunk {chunk_count} failed — inserting {est_ms}ms silence placeholder.")
                failed_chunks.append(chunk_count)

                if audio_enc == "LINEAR16":
                    # Detect SR from previous successful chunk, or default to 24k
                    sr = get_sample_rate_from_fmt(fmt_chunk) if fmt_chunk else 24000
                    silence = generate_silence_payload(est_ms, sample_rate=sr) 
                    audio_payloads.append(silence)
                return

            if audio_enc == "LINEAR16":
                parsed_fmt, parsed_payload = parse_wav_fmt(chunk_bytes)
                if parsed_fmt is None or parsed_payload is None:
                    print(f"    [!] Warning: Could not parse WAV chunk {chunk_count}. Attempting fallback.")
                    if fmt_chunk is None and len(chunk_bytes) >= 44:
                        fmt_chunk = chunk_bytes[12:36] 
                    if len(chunk_bytes) > 44:
                        audio_payloads.append(chunk_bytes[44:])
                    return

                # Capture fmt chunk from the first successful response
                if fmt_chunk is None:
                    fmt_chunk = parsed_fmt

                audio_payloads.append(parsed_payload)
            else:
                mp3_buffer.extend(chunk_bytes)

            print(f"    -> Processed chunk {chunk_count} ({len(chunk_bytes)} bytes).")

        # --- Fragment Processing Loop ---
        for frag in fragments:
            frag_len = len(frag.encode('utf-8'))
            if current_length + frag_len + len("</speak>") > max_bytes:
                flush_buffer(current_ssml_parts)
                current_ssml_parts = ["<speak>"]
                current_length     = len("<speak>")
            current_ssml_parts.append(frag)
            current_length += frag_len

        if len(current_ssml_parts) > 1:
            flush_buffer(current_ssml_parts)

        # --- Assemble Final Audio ---
        final_audio_bytes = b""

        if audio_enc == "LINEAR16":
            if fmt_chunk and audio_payloads:
                final_audio_bytes = build_wav_file(fmt_chunk, audio_payloads)
            else:
                print("    [!] Error: No valid WAV data collected.")
        else:
            final_audio_bytes = bytes(mp3_buffer)

        # --- Save File ---
        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{section_num}"

        filename    = f"{section_num:02d}_{safe_slug}_{voice_name}_{str(rate)}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out: out.write(final_audio_bytes)
            print(f"    -> Saved: {output_path}")
            generated_files.append(filename)
        else:
            print("    [!] Error: No audio generated for this section.")

        # Log debug info
        section_debug_entry = {"section": section_num, "analysis": section_debug}
        if failed_chunks:
            section_debug_entry["failed_chunks"] = failed_chunks
            section_debug_entry["note"] = "Silence placeholders inserted."
        full_debug_log.append(section_debug_entry)

        save_cache()

    # --- Post-Processing ---
    if dry_run:
        # Simple cost estimation logic for display
        if    "Studio"   in voice_name: cost_per_m = 160.0
        elif  "Chirp3"   in voice_name: cost_per_m = 30.0
        elif  "Standard" in voice_name: cost_per_m = 4.0
        else: cost_per_m = 16.0 # WaveNet, Neural2, Polyglot
        
        est_cost = (total_chars / 1_000_000) * cost_per_m
        print(f"\n:: DRY RUN COMPLETE")
        print(f":: Total Characters: {total_chars}")
        print(f":: Estimated Cost: ~${est_cost:.4f} USD (based on voice type)")
        
    elif generated_files:
        playlist_path = os.path.join(output_dir, "playlist.m3u")
        with open(playlist_path, "w", encoding="utf-8") as f:
            for fname in generated_files:
                f.write(f"{fname}\n")
        print(f":: Playlist created: {playlist_path}")

    save_cache()
    print(":: Transcription cache updated.")

    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)
    print(f":: Debug log written to: {debug_path}")

if __name__ == "__main__":
    try:
        generate_audio()

    except KeyboardInterrupt:
        save_cache()
        print("\n:: Interrupted. Cache saved.")
