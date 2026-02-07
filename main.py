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
    │  mark (;) invert the slope to an "updrift" contour (default:
    │  −5% → +10%), producing a rising terminal.
    │
    │  Clause Boundary Reset: At commas, colons, and medial stops (·),
    │  the baseline rewinds by a configurable fraction of the sentence
    │  length, simulating the partial intonation reset observed at
    │  clause boundaries in reconstructed delivery.
    │
    ▼
[3] PHONOLOGY ENGINE (Cached)
    │
    │  Transcribes each word to IPA via CLTK (Probert reconstruction,
    │  Attic dialect). Results are cached in transcription_cache.json
    │  with automatic invalidation when config.toml or the script
    │  itself changes (MD5 comparison). The cache is saved atomically
    │  after every section to prevent data loss during long batches.
    │
    │  Post-transcription corrections:
    │
    │  ● Source-Driven Quantity Enforcement: Rather than blindly
    │    lengthening every ɛ/ɔ in the IPA output, the engine walks
    │    the Greek source characters in parallel with the IPA string
    │    to determine which vowels derive from inherently long
    │    graphemes (η, ω) and applies the length marker (ː) only to
    │    those positions. Short vowels that happen to share an IPA
    │    symbol are left untouched.
    │
    │  ● Gamma Nasalization: Enforces [ŋ] before velars — γγ → [ŋɡ],
    │    γκ → [ŋk], γχ → [ŋx], γξ → [ŋks].
    │
    │  ● IPA Normalization: Replaces German uvular /ʁ/ and English
    │    approximant /ɹ/ with the alveolar trill /r/ appropriate to
    │    reconstructed Attic.
    │
    │  ● Smart Aspirate Injection: Prepends /h/ for rough breathing
    │    (dasia) only when the IPA does not already begin with an
    │    aspirate, preventing double-aspiration artifacts. Rho with
    │    rough breathing (ῥ) is handled separately.
    │
    │  ● Accent Stripping: All pitch information is removed from the
    │    IPA (stress marks, combining accents) so the TTS engine
    │    produces a tonally flat base. Pitch is then reintroduced
    │    exclusively through SSML <prosody contour>, giving us full
    │    control.
    │
    ▼
[4] ACCENT MAPPING — Greek-to-IPA Alignment
    │
    │  Accent type (acute, circumflex, grave) and position are detected
    │  from the NFD-decomposed Greek source text, never from IPA. The
    │  accented vowel's position is then mapped to the corresponding
    │  IPA segment through a three-phase alignment:
    │
    │  Phase 1: Greek vowel units are identified, with recognized
    │  diphthongs (αι, ει, οι, αυ, ευ, ου, ηυ, υι) collapsed into
    │  single vocalic units. Diaeresis (trema) is respected as a
    │  diphthong breaker.
    │
    │  Phase 2: IPA vowel units are identified, grouping consecutive
    │  vowels and length markers into units that correspond to CLTK's
    │  diphthong and long-vowel representations.
    │
    │  Phase 3: The n-th Greek vowel unit is aligned to the n-th IPA
    │  vowel unit, which is robust to epenthesis, contraction, and
    │  diphthong-to-monophthong asymmetries that break naive
    │  character-counting approaches.
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
    │  enclitics (τε, γε, τις), and elided forms (ἀλλ᾽, δ᾽) are merged
    │  into prosodic groups before SSML generation. Each word in the
    │  group is transcribed individually by CLTK, then the IPA strings
    │  are concatenated and wrapped in a single <phoneme> tag. The
    │  accent of the host word governs the group's pitch contour;
    │  proclitic and enclitic accents are suppressed.
    │
    │  Breath pacing: A configurable set of conjunction and preposition
    │  triggers (καί, ἀλλά, ὅτι, etc.) insert natural breath pauses
    │  when the word count since the last pause exceeds a threshold.
    │  A hard ceiling forces a pause regardless of trigger presence.
    │
    │  The fragment stream is chunked into segments under 5000 bytes
    │  (configurable) to respect API limits.
    │
    ▼
[7] AUDIO RENDERER
    │
    │  Sends SSML chunks to Google Cloud TTS with exponential-backoff
    │  retry on transient errors (503, 429, timeouts).
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
    │  content (counting phoneme tags and break durations) and inserts
    │  a correctly-sized PCM silence placeholder. This preserves
    │  temporal alignment in the output file rather than allowing
    │  words to jump forward in time. Failed chunk indices are logged
    │  in the debug output.
    │
    │  Generates an .m3u playlist for seamless playback of multi-
    │  section output.
    │
    ▼
[8] OUTPUT

    Audio files:    {output_dir}/{nn}_{slug}_{voice}_{rate}.wav
    Debug log:      {debug_file}  (JSON — full SSML, per-word analysis,
                                   accent mapping, downdrift values,
                                   contour strings, failure records)
    IPA cache:      transcription_cache.json
    Playlist:       {output_dir}/playlist.m3u

================================================================================
C O N F I G U R A T I O N — config.toml
================================================================================

[files]
    input_text                              Source text path.
    debug_file                              Debug JSON dump path.

[options]
    dry_run                                 Bool. Skip API calls; estimate cost.
    apply_sandhi                            Bool. Merge elided words.
    apply_rough_breathing                   Bool. Pronounce the dasia as /h/.

[prosody]
    contour_peak                            Int.   Acute pitch rise (%).
    contour_grave                           Int.   Grave pitch rise (%).
    contour_end                             Int.   Post-accent pitch drop (%).
    circumflex_tail_len                     Int.   Circumflex fall duration (legacy;
                                                   now bounded by syllable proportion).
    downdrift_start                         Int.   Sentence-initial baseline (%).
    downdrift_end                           Int.   Sentence-final baseline (%).
    updrift_start                           Int.   Interrogative start pitch (%).
    updrift_end                             Int.   Interrogative end pitch (%).
    heavy_word_rate                         Str.   Speed reduction for heavy words.
    downdrift_clause_based_rewind_scale     Float. Clause-boundary baseline reset
                                                   (0.0 = no reset, 1.0 = full).

[pauses]
    breath, newline, comma, period, minor   Str.   Duration in ms (e.g., "145ms").

[pacing]
    force_breath_words                      Int.   Hard ceiling before forced pause.
    max_breath_words                        Int.   Soft target phrase length.

[processing]
    max_chunk_bytes                         Int.   Max SSML bytes per API call.
    delimiter                               Str.   Section separator in input file.

[tts]
    voice_name                              Str.   Google voice ID.
    speaking_rate                           Float. Global speed multiplier.
    pitch                                   Float. Global pitch offset.
    audio_encoding                          Str.   "LINEAR16" or "MP3".
    output_dir                              Str.   Output directory.

[cltk]
    dialect                                 Str.   CLTK dialect (e.g., "attic").
    reconstruction                          Str.   CLTK reconstruction (e.g., "probert").

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
                    the Text-to-Speech API enabled.

    tomli           TOML parser for Python < 3.11 (3.11+ uses stdlib tomllib).

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
IPA_VOWELS = set("aeiouyɛɔæøəɪʊʏ")

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
    if n >= 20:
        if n in GREEK_NUM_BASICS:
            words.append(GREEK_NUM_BASICS[n])
        elif n in GREEK_TENS:
            words.append(GREEK_TENS[n])
        else:
            tens  = (n // 10) * 10
            units = n % 10
            if tens == 20: words.append(GREEK_NUM_BASICS[20])
            else:          words.append(GREEK_TENS.get(tens, ""))
            if units > 0:  words.append(GREEK_NUM_BASICS.get(units, ""))
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
    Transliterates Greek to Latin, optimized for German TTS pronunciation quirks.

    Processes each whitespace-delimited token independently so that
    rough-breathing 'h' is prepended only to the word that carries the
    dasia, not to the entire multi-word string.

    NOTE ON LOSSY TRANSFORMS:
    The diphthong replacements below are INTENTIONALLY LOSSY. Greek accent
    marks (combining acute, grave, circumflex, breathing marks) that sit
    between or on diphthong vowels are silently discarded. This is correct
    behavior: the romanized text is a throwaway visual label inside SSML
    <phoneme> tags — the TTS engine never reads it. All actual pronunciation
    is controlled by the IPA in the 'ph' attribute, and all pitch information
    is controlled by <prosody contour>. The romanized text exists solely to
    satisfy the SSML parser's requirement for visible text content.

    If you need a scholarly romanization that preserves accent information,
    do NOT use this function — it is purpose-built for the TTS pipeline.
    """
    mapping = {
        'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z',
        'η': 'ê', 'θ': 'th','ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm',
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
        'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph','χ': 'ch','ψ': 'ps',
        'ω': 'ô'
    }
    apply_rough = config.get("options", {}).get("apply_rough_breathing", True)

    tokens = text.split()
    romanized_tokens = []

    for token in tokens:
        norm = unicodedata.normalize('NFD', token)

        # Diphthong handling for German phonology.
        # Combining marks between vowels are discarded — see docstring.
        norm = re.sub(r'([εΕ])([\u0300-\u036F]*)([υΥ])', r'e-u', norm)
        norm = re.sub(r'([αΑ])([\u0300-\u036F]*)([υΥ])', r'au', norm)
        norm = re.sub(r'([οΟ])([\u0300-\u036F]*)([υΥ])', r'u', norm)

        result = []

        # Per-token rough breathing check
        if apply_rough and '\u0314' in norm:
            if not token.lower().startswith('ῥ'):
                result.append('h')

        for char in norm:
            if char == '\u0314': continue

            c = char.lower()
            if   c in mapping:    result.append(mapping[c])
            elif 'a' <= c <= 'z': result.append(char)
            elif char == '-':     result.append(char)
            elif char.isspace():  result.append(char)
            elif '\u0300' <= char <= '\u036F': continue

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

def has_greek_chars(text):
    return bool(re.search(r'[\u0370-\u03FF\u1F00-\u1FFF]', text))

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

def find_accent_in_greek(word):
    """
    Finds the accent type and the index of the accented vowel in the
    NFD-decomposed Greek word. Returns (accent_type, vowel_index) where
    vowel_index is the position of the base vowel character that carries
    the accent, counting only base (non-combining) characters.

    This is done on the Greek source text, not on IPA, so it is immune
    to IPA transformations.
    """
    norm = unicodedata.normalize('NFD', word)

    accent_type = "none"
    # Track which base-character index we are at
    base_idx = -1
    found_vowel_idx = -1

    greek_vowels = set("αεηιουωΑΕΗΙΟΥΩ")

    i = 0
    while i < len(norm):
        char = norm[i]

        if '\u0300' <= char <= '\u036F':
            # This is a combining mark — check what kind
            if char == '\u0342':  # Combining Greek Perispomeni (Circumflex)
                accent_type = "circumflex"
                found_vowel_idx = base_idx
            elif char == '\u0301':  # Combining Acute
                accent_type = "acute"
                found_vowel_idx = base_idx
            elif char == '\u0300':  # Combining Grave
                accent_type = "grave"
                found_vowel_idx = base_idx
            i += 1
            continue

        # This is a base character
        base_idx += 1

        # If we already found an accent, stop looking
        if accent_type != "none":
            # We found the accent on the previous base char, break
            # Actually we found it when we saw the combining mark,
            # and found_vowel_idx is already set. Keep scanning in
            # case a later accent overrides (shouldn't happen in
            # well-formed Greek, but be safe).
            pass

        i += 1

    return accent_type, found_vowel_idx

def map_greek_vowel_index_to_ipa(word, greek_vowel_idx, ipa_string):
    """
    Given the index of the accented vowel in the Greek word (counting
    only base characters), find the corresponding vowel position in
    the IPA string.

    Strategy: Build a consonant-vowel skeleton for both the Greek word
    and the IPA string, then align them using the skeleton structure
    rather than assuming a naive 1:1 vowel correspondence.

    Greek diphthongs (αι, ει, οι, αυ, ευ, ου, ηυ, υι) are treated as
    single vocalic units on the Greek side and matched to however many
    IPA segments CLTK produced for them.
    """
    norm = unicodedata.normalize('NFD', word)
    greek_vowel_chars = set("αεηιουωΑΕΗΙΟΥΩ")
    greek_diphthong_seconds = set("ιυΙΥ")

    # --- Phase 1: Build Greek vowel-unit list ---
    # Each entry: (base_char_index, is_diphthong)
    # A diphthong's index is the index of its first vowel.
    greek_vowel_units = []
    base_idx = -1
    i = 0
    chars = list(norm)

    while i < len(chars):
        char = chars[i]

        if '\u0300' <= char <= '\u036F':
            i += 1
            continue

        base_idx += 1
        if char.lower() in greek_vowel_chars:
            # Look ahead past combining marks for a diphthong second element
            j = i + 1
            while j < len(chars) and '\u0300' <= chars[j] <= '\u036F':
                j += 1

            is_diphthong = False
            if j < len(chars) and chars[j].lower() in greek_diphthong_seconds:
                # Check if this is a recognized diphthong pair
                pair = char.lower() + chars[j].lower()
                if pair in {"αι", "ει", "οι", "αυ", "ευ", "ου", "ηυ", "υι"}:
                    # Check for diaeresis (trema) which breaks the diphthong
                    # Diaeresis is U+0308
                    has_diaeresis = False
                    for k in range(j + 1, len(chars)):
                        if '\u0300' <= chars[k] <= '\u036F':
                            if chars[k] == '\u0308':
                                has_diaeresis = True
                                break
                        else:
                            break
                    if not has_diaeresis:
                        is_diphthong = True

            greek_vowel_units.append((base_idx, is_diphthong))
            if is_diphthong:
                # Skip past the second vowel and its combining marks
                i = j + 1
                base_idx += 1
                # Also skip combining marks after the second vowel
                while i < len(chars) and '\u0300' <= chars[i] <= '\u036F':
                    i += 1
                continue

        i += 1

    # --- Phase 2: Identify which vowel unit carries the accent ---
    target_unit = -1
    for unit_idx, (char_idx, _) in enumerate(greek_vowel_units):
        if char_idx == greek_vowel_idx:
            target_unit = unit_idx
            break
        # For diphthongs, the accent index might point to the first char
        # of the pair, which is what we stored
        if char_idx <= greek_vowel_idx:
            target_unit = unit_idx

    if target_unit < 0:
        return -1

    # --- Phase 3: Build IPA vowel-unit list ---
    # Walk the IPA string and group consecutive vowels (including length
    # markers) into units. A vowel followed by ː is one unit. Two vowels
    # in sequence (IPA diphthong from CLTK) are one unit.
    ipa_vowel_units = []  # Each entry: index of the first vowel char
    j = 0
    while j < len(ipa_string):
        ch = ipa_string[j]
        if ch.lower() in IPA_VOWELS:
            unit_start = j
            j += 1
            # Consume length markers and immediately following vowels
            # (CLTK diphthong representations like 'ai', 'oi')
            while j < len(ipa_string):
                next_ch = ipa_string[j]
                if next_ch == 'ː':
                    j += 1
                elif next_ch.lower() in IPA_VOWELS:
                    # Check if this looks like a diphthong (two vowels
                    # with no intervening consonant)
                    j += 1
                else:
                    break
            ipa_vowel_units.append(unit_start)
        else:
            j += 1

    # --- Phase 4: Align ---
    if target_unit < len(ipa_vowel_units):
        return ipa_vowel_units[target_unit]

    # Fallback: if we have more Greek units than IPA units, return the last
    if ipa_vowel_units:
        return ipa_vowel_units[-1]

    return -1

def analyze_word_data(word):
    """
    Robust Philological Analysis.
    1. Transcribes to IPA via CLTK.
    2. Enforces Quantity (Vowel Length) for Eta/Omega by checking the
       Greek source character, not by pattern-matching IPA symbols.
    3. Strips IPA pitch accents (so they don't conflict with our SSML contours).
    4. Detects accent position from the Greek source text and maps it
       to the corresponding position in the cleaned IPA.
    """
    if not word.strip(): return None

    cache = TRANSCRIPTION_CACHE["words"]
    if word in cache:
        return cache[word]

    try:
        raw_ipa  = TRANSCRIBER.transcribe(word)
        norm_ipa = unicodedata.normalize('NFD', raw_ipa)

        # 1. Detect Accent from GREEK SOURCE (not IPA)
        accent_type, greek_vowel_idx = find_accent_in_greek(word)

        # 2. Clean IPA for Audio Generation
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—\']', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "")

        # 3. STRIP ACCENTS from IPA
        clean_ipa = re.sub(r'[\u0300\u0301\u0342\u030d\u0311]', '', clean_ipa)
        clean_ipa = clean_ipa.replace('ˈ', '').replace('ˌ', '')
        clean_ipa = unicodedata.normalize('NFC', clean_ipa)

        # 4. Gamma Nasalization
        for g_char in ['g', 'ɡ']:
            clean_ipa = clean_ipa.replace(f'{g_char}{g_char}', f'ŋ{g_char}')
            clean_ipa = clean_ipa.replace(f'{g_char}k', f'ŋk')
            clean_ipa = clean_ipa.replace(f'{g_char}χ', f'ŋχ')
            clean_ipa = clean_ipa.replace(f'{g_char}ξ', f'ŋξ')
            clean_ipa = clean_ipa.replace(f'{g_char}x', f'ŋx')

        # 5. IPA Normalization (Trilled R)
        clean_ipa = clean_ipa.replace('ʁ', 'r').replace('ɹ', 'r')

        # 6. QUANTITY ENFORCEMENT — Source-Character-Driven
        # Instead of blindly lengthening every ɛ/ɔ in the IPA output,
        # we identify which Greek characters are inherently long (η, ω)
        # and apply the length marker only to their corresponding IPA
        # vowels. This prevents spurious lengthening if CLTK ever
        # produces ɛ or ɔ for non-eta/omega reasons.
        clean_ipa = _enforce_quantity_from_source(word, clean_ipa)

        # 7. Rough Breathing
        norm_greek  = unicodedata.normalize('NFD', word)
        apply_rough = config.get("options", {}).get("apply_rough_breathing", True)

        if apply_rough and '\u0314' in norm_greek:
            if not word.lower().startswith('ῥ'):
                if not (clean_ipa.startswith('h') or clean_ipa.startswith('ʰ')):
                    clean_ipa = 'h' + clean_ipa

        # 8. Map accent position from Greek to IPA
        accent_idx = -1
        if accent_type != "none" and greek_vowel_idx >= 0:
            accent_idx = map_greek_vowel_index_to_ipa(word, greek_vowel_idx, clean_ipa)

        # Fallback: Greek Text Circumflex
        if accent_type == "none":
            norm_greek_check = unicodedata.normalize('NFD', word)
            if '\u0342' in norm_greek_check:
                accent_type = "circumflex"
                match = re.search(r'[' + ''.join(IPA_VOWELS) + r']', clean_ipa)
                if match: accent_idx = match.start()

        long_markers    = clean_ipa.count('ː')
        has_long_vowels = bool(re.search(r'[ηω]', word))
        is_heavy        = long_markers > 0 or has_long_vowels

        data = {
            "raw_ipa":     raw_ipa,
            "ipa":         clean_ipa,
            "accent_type": accent_type,
            "accent_idx":  accent_idx,
            "is_heavy":    is_heavy,
            "len":         len(clean_ipa)
        }
        cache[word] = data
        return data

    except Exception as e:
        print(f"    [!] IPA Transcription failed for '{word}': {e}")
        return None

def _enforce_quantity_from_source(greek_word, ipa_string):
    """
    Walks the Greek source characters and the IPA string in parallel,
    identifying vowels that derive from η or ω and ensuring their IPA
    counterparts carry the length marker (ː). Vowels from other sources
    (ε, ο, or any context where CLTK produced ɛ/ɔ for non-long-vowel
    reasons) are left untouched.
    """
    norm = unicodedata.normalize('NFD', greek_word)
    inherently_long = set("ηωΗΩ")
    greek_vowel_chars = set("αεηιουωΑΕΗΙΟΥΩ")

    # Build list of Greek vowel positions and whether each is long
    greek_vowels_long = []
    for char in norm:
        if '\u0300' <= char <= '\u036F':
            continue
        if char.lower() in greek_vowel_chars:
            greek_vowels_long.append(char.lower() in inherently_long)

    # Walk IPA and find vowel positions
    ipa_vowel_positions = []
    i = 0
    while i < len(ipa_string):
        if ipa_string[i].lower() in IPA_VOWELS:
            # Check if already followed by ː
            already_long = (i + 1 < len(ipa_string) and ipa_string[i + 1] == 'ː')
            ipa_vowel_positions.append((i, already_long))
            if already_long:
                i += 2
            else:
                i += 1
        else:
            i += 1

    # Align: for each pair (greek_vowel_n, ipa_vowel_n), if the Greek
    # vowel is inherently long and the IPA vowel lacks ː, insert it.
    # Work backwards so insertions don't shift indices.
    insertions = []
    for v_idx in range(min(len(greek_vowels_long), len(ipa_vowel_positions))):
        should_be_long = greek_vowels_long[v_idx]
        ipa_pos, already_long = ipa_vowel_positions[v_idx]

        if should_be_long and not already_long:
            insertions.append(ipa_pos + 1)

    # Apply insertions in reverse order
    ipa_list = list(ipa_string)
    for pos in reversed(insertions):
        ipa_list.insert(pos, 'ː')

    return "".join(ipa_list)

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
            # The rise-fall must complete within the accented syllable.
            # The tail ends at the syllable boundary, not at a fixed
            # offset from the peak.
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

def build_ssml_fragments(full_text):

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

    def scale_time(time_str):
        if not time_str.endswith("ms"): return time_str
        try:
            val = int(time_str.replace("ms", ""))
            return f"{int(val / rate_global)}ms"
        except: return time_str

    t_breath     = scale_time(pauses.get("breath",  "145ms"))
    t_newline    = scale_time(pauses.get("newline", "180ms"))
    t_comma      = scale_time(pauses.get("comma",   "80ms"))
    t_period     = scale_time(pauses.get("period",  "145ms"))
    t_minor      = scale_time(pauses.get("minor",   "215ms"))

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

                    if apply_sandhi and (word.endswith('᾽') or word.endswith('\u2019') or word.endswith("'")):
                        i += 1
                        word = words[i]
                        group.append(word)
                        merged = True
                        continue

                    if group[0].lower() in PROCLITICS and len(group) == 1:
                        i += 1
                        word = words[i]
                        group.append(word)
                        merged = True
                        continue

                    if next_word.lower() in ENCLITICS:
                        i += 1
                        word = words[i]
                        group.append(word)
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

                # Phonology: transcribe each word individually, then concatenate IPA
                group_ipa_parts = []
                group_accent_type = "none"
                group_accent_ipa_idx = -1
                running_ipa_len = 0

                for gw in group:
                    if not has_greek_chars(gw):
                        continue
                    w_data = analyze_word_data(gw)
                    if w_data:
                        group_ipa_parts.append(w_data["ipa"])
                        # Use the accent from the first content word
                        # (proclitics are unaccented, enclitics yield to host)
                        if w_data["accent_type"] != "none" and group_accent_type == "none":
                            group_accent_type = w_data["accent_type"]
                            group_accent_ipa_idx = running_ipa_len + w_data["accent_idx"]
                        running_ipa_len += len(w_data["ipa"])

                combined_ipa = "".join(group_ipa_parts)

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

                # Append space if not at end of chunk
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

def generate_audio():
    input_path = config["files"].get("input_text", "input.txt")
    output_dir = config["tts"].get("output_dir", "output")
    debug_path = config["files"].get("debug_file", "debug_dump.json")

    voice_name = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Enceladus")
    rate       = config["tts"].get("speaking_rate", 1.0)
    audio_enc  = config["tts"].get("audio_encoding", "LINEAR16")
    pitch_val  = config["tts"].get("pitch", 0.0)
    max_bytes  = config["processing"].get("max_chunk_bytes", 4500)
    delimiter  = config["processing"].get("delimiter", "---")

    dry_run = config["options"].get("dry_run", False)

    ext = "wav" if audio_enc == "LINEAR16" else "mp3"

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return

    with open(input_path, "r", encoding="utf-8") as f: content = f.read()
    sections = [s.strip() for s in content.split(delimiter) if s.strip()]

    print(f":: Processing {len(sections)} sections...")
    if dry_run: print(":: DRY RUN MODE: No audio will be generated.")

    client = None
    os.makedirs(output_dir, exist_ok=True)
    if not dry_run:
        client = texttospeech.TextToSpeechClient()

    lang_code    = "-".join(voice_name.split("-")[:2])
    voice_params = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)

    encoding_enum = texttospeech.AudioEncoding.LINEAR16
    if audio_enc == "MP3": encoding_enum = texttospeech.AudioEncoding.MP3

    audio_cfg = texttospeech.AudioConfig(
        audio_encoding=encoding_enum, speaking_rate=rate, pitch=pitch_val
    )

    full_debug_log = []
    generated_files = []
    total_chars = 0

    for sec_idx, text in enumerate(sections):
        total_chars += len(text)
        print(f":: Generating Section {sec_idx+1}...")
        fragments, section_debug = build_ssml_fragments(text)

        full_ssml_string = "".join(fragments)

        if dry_run:
            print(f"    [Dry Run] Section {sec_idx+1} processed.")
            preview = full_ssml_string[:200].replace("\n", " ")
            print(f"    [SSML Preview] {preview}...")

            full_debug_log.append({
                "section": sec_idx+1,
                "mode": "dry_run",
                "ssml": full_ssml_string,
                "analysis": section_debug
            })
            continue

        # --- Audio Generation ---
        current_ssml_parts = ["<speak>"]
        current_length     = len("<speak>")

        fmt_chunk      = None
        audio_payloads = []
        mp3_buffer     = bytearray()

        chunk_count    = 0
        failed_chunks  = []

        # --- WAV silence generator for gap-filling ---
        def generate_silence_payload(duration_ms, sample_rate=24000, sample_width=2):
            """
            Generates raw PCM silence bytes for the given duration.
            Used to fill gaps when an API chunk fails, so the output
            audio maintains correct temporal alignment rather than
            having words jump forward in time.
            """
            num_samples = int(sample_rate * duration_ms / 1000)
            return b'\x00' * (num_samples * sample_width)

        def estimate_chunk_duration_ms(ssml_parts):
            """
            Rough estimate of how long a chunk would sound, based on
            the number of phoneme tags and break durations. Used to
            generate correctly-sized silence placeholders on failure.
            """
            text = "".join(ssml_parts)
            duration = 0

            # Count break tags and sum their durations
            for match in re.finditer(r'<break\s+time="(\d+)ms"\s*/>', text):
                duration += int(match.group(1))

            # Count phoneme tags — rough estimate of 400ms per word
            word_count = len(re.findall(r'<phoneme', text))
            duration += word_count * 400

            return max(duration, 200)  # Minimum 200ms

        def flush_buffer(parts):
            nonlocal fmt_chunk, chunk_count
            parts_for_send = list(parts)
            parts_for_send.append("</speak>")
            ssml_string = "".join(parts_for_send)
            chunk_bytes = fetch_audio_bytes(client, ssml_string, voice_params, audio_cfg)

            chunk_count += 1

            if chunk_bytes is None:
                est_ms = estimate_chunk_duration_ms(parts_for_send)
                print(f"    [!] Chunk {chunk_count} failed — inserting {est_ms}ms silence placeholder.")
                failed_chunks.append(chunk_count)

                if audio_enc == "LINEAR16":
                    silence = generate_silence_payload(est_ms)
                    audio_payloads.append(silence)
                    # If we have no fmt chunk yet, we can't generate valid silence.
                    # The silence bytes will still be appended and will work once
                    # we get a fmt chunk from a later successful request.
                # MP3 silence is non-trivial to generate; just log the gap.
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

                if fmt_chunk is None:
                    fmt_chunk = parsed_fmt

                audio_payloads.append(parsed_payload)
            else:
                mp3_buffer.extend(chunk_bytes)

            print(f"    -> Processed chunk {chunk_count}.")

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

        # --- Generate Filename ---
        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{sec_idx+1}"

        filename    = f"{sec_idx+1:02d}_{safe_slug}_{voice_name}_{str(rate)}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out: out.write(final_audio_bytes)
            print(f"    -> Saved: {output_path}")
            generated_files.append(filename)
        else:
            print("    [!] Error: No audio generated for this section.")

        # Log failed chunks in debug output
        section_debug_entry = {"section": sec_idx+1, "analysis": section_debug}
        if failed_chunks:
            section_debug_entry["failed_chunks"] = failed_chunks
            section_debug_entry["note"] = "Silence placeholders inserted for failed chunks."
        full_debug_log.append(section_debug_entry)

        save_cache()

    # --- Post-Processing ---
    if dry_run:
        est_cost = (total_chars / 1_000_000) * 16.00
        print(f"\n:: DRY RUN COMPLETE")
        print(f":: Total Characters: {total_chars}")
        print(f":: Estimated Cost (WaveNet Pricing — verify for Chirp3-HD): ${est_cost:.2f} USD")
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
