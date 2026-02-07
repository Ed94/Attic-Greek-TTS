"""
================================================================================
A N C I E N T   G R E E K   T T S   G E N E R A T O R
The "German Trojan Horse" Method
================================================================================

Google Cloud TTS (and most commercial engines) does not support Ancient Greek.
Feeding it Greek script results in either Modern Greek pronunciation (wrong
vowels, stress instead of pitch) or total failure.

-----------------------------------------------------------------------------
T H E   S O L U T I O N
-----------------------------------------------------------------------------
We treat the TTS engine as a dumb synthesizer. We calculate the exact
phonemes (IPA) ourselves and wrap them in SSML tags. We use a German voice
model because its phoneme set (pure vowels, aspirated stops) maps significantly
better to Ancient Greek than English models.

To bypass the language filter, we perform a "Trojan Horse" injection:
1. The visual text inside the SSML tag is Romanized (e.g., "Mênin aeide").
2. The actual audio is forced via IPA (International Phonetic Alphabet).
3. We manually impose Pitch Accent and Syllable Quantity via SSML <prosody>.

ARCHITECTURAL PIPELINE:
-----------------------
[Raw Text]
    |
    v
[Normalization & Safety]
    - Cleans Critical Sigla (Removes {}, [], <>, †).
    - Expands Numerals ("24" -> "eikosi tessares", "IV" -> "tettares").
    - Escapes XML special characters to prevent API crashes.
    |
    v
[Sentence Analysis] (The "Downdrift" Engine)
    - Calculates linear pitch baseline (Start +10% -> End -10%).
    - Interrogative Detection: Inverts downdrift to "Updrift" (Tunable)
      for sentences ending in a Greek question mark (;).
    - Clause Detection: Resets intonation at colons/commas.
    |
    v
[Phonology Engine] (Cached)
    - Transcribes to IPA (CLTK / Probert reconstruction).
    - Gamma Nasalization: Enforces [ŋ] for γγ, γκ, γχ, γξ.
    - IPA Normalization: Enforces Alveolar Trill (/r/) over German Uvular (/ʁ/).
    - Quantity Enforcement: Forces length markers (ː) on Eta/Omega.
    - Smart Aspirate: Injects /h/ for Rough Breathing without double-aspiration.
    |
    v
[Prosody Synthesizer]
    - Calculates Pitch relative to the dynamic Sentence Baseline.
    - Acute:      Sharp Rise (+35%) above baseline.
    - Circumflex: Rise-Fall (+35% -> -12%) on target syllable.
    - Grave:      Suppressed pitch (+5%).
    - Heavy Word: Time dilation (-15% speed) to simulate quantity.
    |
    v
[SSML Batcher]
    - Word Grouping: Merges Proclitics (ὁ), Enclitics (τις), and Elisions (ἀλλ᾽)
      into single prosodic units to ensure continuous phonation.
    - Chunks stream into < 5000 byte segments.
    |
    v
[Audio Renderer]
    - Requests audio chunks from Google Cloud.
    - Robust Binary Stitching: Dynamically parses RIFF headers to extract payload.
    - Generates .m3u Playlist for seamless playback of chunked audio.

TUNABLES (config.toml):
-----------------------
[files]
    input_text            :: Path to source text (e.g., "input.txt").
    debug_file            :: Path to debug JSON dump.

[options]
    dry_run               :: (Bool) If True, no API calls are made.
    apply_sandhi          :: (Bool) Merge words ending in apostrophe.
    apply_rough_breathing :: (Bool) Pronounce the 'h' (dasia).

[prosody]
    contour_peak          :: (Int) Pitch rise for Acute accent (e.g., 35).
    contour_grave         :: (Int) Pitch rise for Grave accent (e.g., 5).
    contour_end           :: (Int) Pitch drop after accent (e.g., -12).
    circumflex_tail_len   :: (Int) Duration of circumflex fall.
    downdrift_start       :: (Int) Baseline pitch at sentence start (e.g., 10).
    downdrift_end         :: (Int) Baseline pitch at sentence end (e.g., -10).
    updrift_start         :: (Int) Start pitch for questions (e.g., -5).
    updrift_end           :: (Int) End pitch for questions (e.g., 10).
    heavy_word_rate       :: (Str) Speed slowdown for heavy words (e.g., "-15%").
    downdrift_clause_based_rewind_scale :: (Float) Reset intonation at commas (0.0-1.0).

[pauses]
    breath, newline, comma, period, minor :: (Str) MS duration (e.g., "145ms").

[pacing]
    force_breath_words    :: (Int) Max words allowed before forcing a pause.
    max_breath_words      :: (Int) Ideal phrase length.

[processing]
    max_chunk_bytes       :: (Int) Max SSML size per API call (default 4500).
    delimiter             :: (Str) Separator for input sections (e.g., "---").

[tts]
    voice_name            :: Google Voice ID (e.g., "de-DE-Chirp3-HD-Enceladus").
    speaking_rate         :: Global speed multiplier.
    pitch                 :: Global pitch offset.
    audio_encoding        :: "LINEAR16" (WAV) or "MP3".
    output_dir            :: Directory for generated audio.

CACHING:
--------
IPA transcription is expensive. We maintain 'transcription_cache.json'.
The cache is updated atomically after every section is processed to prevent
data loss during long batch operations.
*Auto-Invalidation*: If config.toml or the script changes, the cache wipes.

DEPENDENCIES:
-------------
Requires 'cltk' with 'grc_models_cltk' downloaded.
The script will auto-detect missing models and provide download instructions.

================================================================================
"""

import hashlib
import os
import re
import sys
import json
import struct
import argparse
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
TRANSCRIPTION_CACHE = {}

# Calculate current state
current_config_hash = get_file_hash("config.toml")
current_script_hash = get_file_hash(__file__)

if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            loaded_cache = json.load(f)
        
        # Check integrity via _meta field
        meta = loaded_cache.get("_meta", {})
        if (meta.get("config_hash") == current_config_hash and 
            meta.get("script_hash") == current_script_hash):
            
            TRANSCRIPTION_CACHE = loaded_cache
            # Subtract 1 for _meta entry
            count = max(0, len(TRANSCRIPTION_CACHE) - 1)
            print(f":: Cache Hit: Loaded {count} lexical entries.")
        else:
            print(":: Change detected in config or script. Invalidating cache.")
            TRANSCRIPTION_CACHE = {}
            
    except Exception as e:
        print(f":: Cache Corrupted ({e}). Starting with empty lexicon.")

# Initialize/Update metadata for the next save
TRANSCRIPTION_CACHE["_meta"] = {
    "config_hash": current_config_hash,
    "script_hash": current_script_hash
}

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

    text = re.sub(r'\b([0-9]+)\b',             replace_match, text)
    text = re.sub(r'\b([ivxIVX]+)\b',          replace_match, text)
    text = re.sub(r'(?<=\d)[a-z]\b|\b[a-z]\b', replace_match, text)
    return text

def romanize_greek(text):
    """ 
    Transliterates Greek to Latin, optimized for German TTS pronunciation quirks.
    """
    norm = unicodedata.normalize('NFD', text)
    
    # 1. Handle Diphthongs specifically for German Phonology
    # 'eu' in German is 'oy', so we break it to 'e-u' to force 'eh-oo'
    norm = re.sub(r'([εΕ])([\u0300-\u036F]*)([υΥ])', r'e\2-u', norm) 
    # 'au' in German is correct for Greek 'au'
    norm = re.sub(r'([αΑ])([\u0300-\u036F]*)([υΥ])', r'a\2u', norm)
    # 'ou' in German is 'u' (long u), which is perfect for Greek 'ou'
    norm = re.sub(r'([οΟ])([\u0300-\u036F]*)([υΥ])', r'u', norm) # ou -> u

    result = []
    mapping = {
        'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z', 
        'η': 'ê', 'θ': 'th','ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm', 
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 
        'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph','χ': 'ch','ψ': 'ps', 
        'ω': 'ô'
    }
    
    apply_rough = config.get("options", {}).get("apply_rough_breathing", True)
    
    # Handle Rough Breathing (Dasia)
    if apply_rough and '\u0314' in norm:
        if not text.lower().startswith('ῥ'):
             result.append('h')

    for char in norm:
        if char == '\u0314': continue # Skip breathing mark
        
        c = char.lower()
        if   c in mapping:    result.append(mapping[c])
        # Allow existing Latin chars (from our regex fixes)
        elif 'a' <= c <= 'z': result.append(char) 
        elif char == '-':     result.append(char) # Keep the hyphen we added
        elif char.isspace():  result.append(char)
        
    return "".join(result)

def has_greek_chars(text):
    return bool(re.search(r'[\u0370-\u03FF\u1F00-\u1FFF]', text))

def sanitize_filename(text):
    text = re.sub(r'[\s\n\r]+', '_', text)
    text = re.sub(r'[^\w\-\u0370-\u03FF\u1F00-\u1FFF]', '', text)
    return text[:50].strip('_')

# ==============================================================================
# 4. P H O N O L O G Y   &   P R O S O D Y
# ==============================================================================

def analyze_word_data(word):
    """
    Robust Philological Analysis.
    1. Transcribes to IPA.
    2. Enforces Quantity (Vowel Length) for Eta/Omega.
    3. Strips IPA pitch accents (so they don't conflict with our SSML contours).
    4. Detects accent position for the SSML engine.
    """
    # Skip caching for _meta key
    if word == "_meta": return None
    
    if word in TRANSCRIPTION_CACHE:
        return TRANSCRIPTION_CACHE[word]
    if not word.strip(): return None

    try:
        raw_ipa  = TRANSCRIBER.transcribe(word)
        # Normalize to break apart combining characters (like accents)
        norm_ipa = unicodedata.normalize('NFD', raw_ipa)
        
        # 1. Detect Accent (BEFORE we strip it)
        accent_type = "none"
        accent_idx  = -1
        
        # Clean IPA for indexing (remove brackets temporarily)
        temp_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "").replace(" ", "")
        
        for i, char in enumerate(temp_ipa):
            if char in ["\u0342", "ˆ"]: # Circumflex
                accent_type = "circumflex"
                accent_idx  = i
                break
            elif char in ["\u0301", "´"]: # Acute
                accent_type = "acute"
                accent_idx  = i
                break
            elif char in ["\u0300", "`"]: # Grave
                accent_type = "grave"
                accent_idx  = i
                break
        
        # 2. Clean IPA for Audio Generation
        # Remove brackets, slashes, and punctuation
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "")

        # 3. STRIP ACCENTS from IPA
        # We want the TTS engine to be 'flat' so our SSML <prosody> controls the pitch perfectly.
        # If we leave accents in, the engine fights our SSML.
        clean_ipa = re.sub(r'[\u0300\u0301\u0342\u030d\u0311]', '', clean_ipa)

        # 4. Gamma Nasalization (Angelos Rule)
        clean_ipa = clean_ipa.replace("gg", "ŋg").replace("gk", "ŋk").replace("gχ", "ŋχ").replace("gξ", "ŋξ")

        # 5. IPA Normalization (Trilled R)
        clean_ipa = clean_ipa.replace('ʁ', 'r').replace('ɹ', 'r')
        
        # 6. QUANTITY ENFORCEMENT (The Vowel Length Fix)
        # CLTK Probert usually maps:
        # Eta (η) -> ɛ (open e)
        # Omega (ω) -> ɔ (open o)
        # We ensure these ALWAYS have the length marker (ː)
        
        # Regex: Find 'ɛ' or 'ɔ' NOT followed by 'ː', and add 'ː'
        clean_ipa = re.sub(r'([ɛɔ])(?!ː)', r'\1ː', clean_ipa)

        # 7. Rough Breathing
        norm_greek  = unicodedata.normalize('NFD', word)
        apply_rough = config.get("options", {}).get("apply_rough_breathing", True)
        
        if apply_rough and '\u0314' in norm_greek:
            if not word.lower().startswith('ῥ'):
                # Only prepend 'h' if the IPA doesn't already have 'h'
                if not (clean_ipa.startswith('h') or clean_ipa.startswith('ʰ')):
                    clean_ipa = 'h' + clean_ipa

        # Fallback: IPA Stress (ˈ) if no pitch accent found
        if accent_type == "none":
            if 'ˈ' in clean_ipa:
                accent_type = "acute" 
                # Recalculate index based on stripped string
                accent_idx  = clean_ipa.find('ˈ') + 1 
                clean_ipa   = clean_ipa.replace('ˈ', '')
        
        # Fallback: Greek Text Circumflex
        if accent_type == "none":
            if '\u0342' in norm_greek or '͂' in norm_greek:
                accent_type = "circumflex"
                # Find the vowel to attach it to
                match = re.search(r'[aeiouyɛɔηω]', clean_ipa)
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
            "len":         len(clean_ipa) # Length of the actual spoken IPA
        }
        TRANSCRIPTION_CACHE[word] = data
        return data

    except Exception as e:
        return None

def calculate_prosody(word_data, baseline_shift=0):
    if not word_data: return None, "0%"
    
    ipa    = word_data["ipa"]
    a_type = word_data["accent_type"]
    idx    = word_data["accent_idx"]
    total  = word_data["len"]
    
    # Load config
    c_peak  = config["prosody"].get("contour_peak",  35)
    c_grave = config["prosody"].get("contour_grave", 5)
    c_end   = config["prosody"].get("contour_end",   -12)
    c_tail  = config["prosody"].get("circumflex_tail_len", 15)
    
    val_start = baseline_shift
    val_peak  = baseline_shift + c_peak
    val_grave = baseline_shift + c_grave
    val_end   = baseline_shift + c_end
    
    def p(val): return f"{int(val):+d}%"

    # --- 1. Contour Calculation (Same as before) ---
    contour = None
    if idx >= 0 and total > 0:
        pos_ratio = max(0.1, min(0.9, idx / total))
        peak_pct  = int(pos_ratio * 100)
        
        if a_type == "circumflex":
            tail_pct = min(peak_pct + c_tail, 100)
            contour = f"(0%,{p(val_start)}) ({peak_pct}%,{p(val_peak)}) ({tail_pct}%,{p(val_end)}) (100%,{p(val_end)})"
        elif a_type == "grave":
            contour = f"(0%,{p(val_start)}) ({peak_pct}%,{p(val_grave)}) (100%,{p(val_end)})"
        else: 
            contour = f"(0%,{p(val_start)}) ({peak_pct}%,{p(val_peak)}) (100%,{p(val_end)})"

    # --- 2. Improved "Heavy Word" Smoothing ---
    rate = "0%"
    if word_data["is_heavy"]:
        # Count syllables (rough approximation via vowels)
        vowel_count = len(re.findall(r'[aeiouyɛɔηω]', ipa, re.IGNORECASE))
        
        # LOGIC: Only slow down if the word is substantial (3+ syllables)
        # or if it is extremely dense with long vowels.
        base_slowdown = int(config["prosody"].get("heavy_word_rate", "-15%").strip('%'))
        
        if vowel_count < 2:
            # Short words (e.g., 'mē') shouldn't drag.
            rate = "0%" 
        elif vowel_count == 2:
            # Mild slowdown for disyllabic words
            rate = f"{int(base_slowdown / 2)}%" 
        else:
            # Full slowdown for long, complex words
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
    drift_start  = config["prosody"].get("downdrift_start", 10)
    drift_end    = config["prosody"].get("downdrift_end", -10)
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
        
        # IMPROVEMENT 1: Interrogative Intonation (Tunable Updrift)
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
                elif part in ['·', '-', ':']:  t = t_minor # Colon is distinct/longer
                fragments.append(f'<break time="{t}"/>')
                words_since_breath = 0 
                
                # Clause-Based Intonation Reset
                if total_sentence_words > 0:
                    rewind_amount = int(total_sentence_words * rewind_scale)
                    current_word_idx = max(0, current_word_idx - rewind_amount)

                continue
            
            words = part.split()
            
            # Enclitic / Proclitic / Sandhi Merging
            i = 0
            while i < len(words):
                word = words[i]
                merged = True
                
                while merged and i + 1 < len(words):
                    merged = False
                    next_word = words[i+1]
                    if not has_greek_chars(next_word): break

                    if apply_sandhi and (word.endswith('᾽') or word.endswith('’') or word.endswith("'")):
                        word += next_word
                        i += 1
                        merged = True
                        continue
                    
                    if word.lower() in PROCLITICS:
                        word += next_word
                        i += 1
                        merged = True
                        continue
                        
                    if next_word.lower() in ENCLITICS:
                        word += next_word
                        i += 1
                        merged = True
                        continue

                if not has_greek_chars(word): 
                    if word.strip(): fragments.append(escape(word))
                    i += 1
                    continue

                words_since_breath += 1
                if words_since_breath > max_breath:
                    if is_breath_trigger(word) or words_since_breath >= force_breath:
                        fragments.append(f'<break time="{t_breath}"/>')
                        words_since_breath = 0
                
                # Downdrift
                if total_sentence_words > 1:
                    position_ratio = current_word_idx / (total_sentence_words - 1)
                else:
                    position_ratio = 0.0
                
                current_baseline  = current_drift_start + ((current_drift_end - current_drift_start) * position_ratio)
                current_word_idx += 1

                # Phonology
                w_data     = analyze_word_data(word)
                dummy_text = romanize_greek(word)
                
                if w_data and dummy_text:
                    ipa               = w_data["ipa"]
                    contour, dur_rate = calculate_prosody(w_data, baseline_shift=current_baseline)
                    
                    # SSML Safety (Escape XML chars)
                    safe_text  = escape(dummy_text)
                    ph_tag     = f'<phoneme alphabet="ipa" ph="{ ipa }">{ safe_text }</phoneme>'
                    final_ssml = ph_tag
                    
                    if dur_rate != "0%":
                        final_ssml = f'<prosody rate="{dur_rate}">{final_ssml}</prosody>'
                    if contour:
                        final_ssml = f'<prosody contour="{contour}">{final_ssml}</prosody>'
                    
                    fragments.append(final_ssml)
                    
                    debug_entries.append({
                        "greek":     word,
                        "roman":     dummy_text,
                        "downdrift": int(current_baseline),
                        "contour":   contour
                    })
                else:
                    fragments.append(escape(dummy_text))
                
                # Append space if we are not at end of chunk (Sandhi merge logic removed as it is handled above)
                if i < len(words) - 1: 
                    fragments.append(" ")
                
                i += 1

    return fragments, debug_entries

# ==============================================================================
# 6. A U D I O  R E N D E R E R
# ==============================================================================

def fix_wav_header(wav_bytes):
    if len(wav_bytes) < 44: return wav_bytes
    total_size     = len(wav_bytes)
    chunk_size     = total_size - 8
    subchunk2_size = total_size - 44
    new_header     = wav_bytes[:4] + struct.pack('<I', chunk_size) + wav_bytes[8:40] + struct.pack('<I', subchunk2_size) + wav_bytes[44:]
    return new_header

def extract_wav_payload(wav_bytes):
    """
    Robustly parses RIFF/WAVE structure to find the 'data' chunk.
    This prevents corruption if Google adds metadata headers.
    """
    if len(wav_bytes) < 12: return b""
    
    # Check RIFF header
    if wav_bytes[0:4] != b'RIFF': return b""
    if wav_bytes[8:12] != b'WAVE': return b""

    # Start searching after the 12-byte header
    pos = 12
    length = len(wav_bytes)

    while pos + 8 < length:
        # Read Chunk ID (4 bytes) and Size (4 bytes, little endian)
        chunk_id = wav_bytes[pos : pos+4]
        try:
            chunk_size = struct.unpack('<I', wav_bytes[pos+4 : pos+8])[0]
        except struct.error:
            break # Malformed tail

        if chunk_id == b'data':
            # FOUND IT: Return the audio data inside this chunk
            return wav_bytes[pos+8 : pos+8+chunk_size]
        
        # If not 'data', skip this chunk and look at the next one
        # (+8 accounts for the ID and Size bytes we just read)
        pos += 8 + chunk_size
        
        # Safety alignment (WAV chunks must be word-aligned)
        if chunk_size % 2 == 1:
            pos += 1

    # Fallback: If parsing fails, assume standard header size (44 bytes)
    # This catches cases where the file might be raw PCM but labelled WAV
    return wav_bytes[44:]

def fetch_audio_bytes(client, ssml_chunk, voice_params, audio_config):
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml_chunk)
    try:
        response = client.synthesize_speech(
            request = texttospeech.SynthesizeSpeechRequest(
                input = synthesis_input, voice=voice_params, audio_config=audio_config
            )
        )
        return response.audio_content
    except Exception as e:
        print(f"    -> API Error: {e}")
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
    
    print(f":: Processing { len(sections) } sections...")
    if dry_run: print(":: DRY RUN MODE: No audio will be generated.")
    
    client = None
    # Ensure output directory exists even for dry runs (for debug/playlist files)
    os.makedirs(output_dir, exist_ok = True) 
    if not dry_run:
        client = texttospeech.TextToSpeechClient()
        os.makedirs(output_dir, exist_ok = True)
    
    lang_code    = "-".join(voice_name.split("-")[:2])
    voice_params = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
    
    encoding_enum = texttospeech.AudioEncoding.LINEAR16
    if audio_enc == "MP3": encoding_enum = texttospeech.AudioEncoding.MP3
    
    audio_cfg = texttospeech.AudioConfig(
        audio_encoding = encoding_enum, speaking_rate = rate, pitch = pitch_val
    )

    full_debug_log = []
    generated_files = [] 
    total_chars = 0

    for i, text in enumerate(sections):
        total_chars += len(text)
        print(f":: Generating Section {i+1}...")
        fragments, section_debug = build_ssml_fragments(text)
        
        # Construct FULL SSML for debug log in both modes
        full_ssml_string = "".join(fragments)
        
        # IMPROVEMENT: Enhanced Debug Dump logic
        if dry_run:
            print(f"    [Dry Run] Section {i+1} processed.")
            # Preview first 200 chars
            preview = full_ssml_string[:200].replace("\n", " ")
            print(f"    [SSML Preview] {preview}...")
            
            full_debug_log.append({ 
                "section": i+1, 
                "mode": "dry_run",
                "ssml": full_ssml_string,
                "analysis": section_debug 
            })
            continue

        current_ssml_parts = ["<speak>"]
        current_length     = len("<speak>")
        final_audio_bytes  = bytearray()
        
        def flush_buffer(parts):
            parts.append("</speak>")
            return fetch_audio_bytes(client, "".join(parts), voice_params, audio_cfg)

        for frag in fragments:
            frag_len = len(frag.encode('utf-8'))
            if current_length + frag_len + len("</speak>") > max_bytes:
                chunk_bytes = flush_buffer(current_ssml_parts)
                if chunk_bytes:
                    if len(final_audio_bytes) == 0:
                        final_audio_bytes += chunk_bytes
                    else:
                        if audio_enc == "LINEAR16":
                            final_audio_bytes += extract_wav_payload(chunk_bytes)
                        else:
                            final_audio_bytes += chunk_bytes
                print(f"    -> Stitched chunk.")
                current_ssml_parts = ["<speak>"]
                current_length     = len("<speak>")
            current_ssml_parts.append(frag)
            current_length += frag_len
        
        if len(current_ssml_parts) > 1:
            chunk_bytes = flush_buffer(current_ssml_parts)
            if chunk_bytes:
                if len(final_audio_bytes) == 0:
                    final_audio_bytes += chunk_bytes
                else:
                    if audio_enc == "LINEAR16":
                        # Use robust payload extraction
                        final_audio_bytes += extract_wav_payload(chunk_bytes)
                    else:
                        final_audio_bytes += chunk_bytes

        if audio_enc == "LINEAR16" and len(final_audio_bytes) > 44:
            final_audio_bytes = fix_wav_header(final_audio_bytes)

        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{i+1}"
        
        filename    = f"{i+1:02d}_{safe_slug}_{voice_name}_{str(rate)}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out: out.write(final_audio_bytes)
            print(f"    -> Saved: {output_path}")
            generated_files.append(filename)
        else:
            print("    [!] Error: No audio generated.")

        full_debug_log.append({ "section": i+1, "analysis": section_debug })
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)

    if dry_run:
        est_cost = (total_chars / 1_000_000) * 16.00
        print(f"\n:: DRY RUN COMPLETE")
        print(f":: Total Characters: {total_chars}")
        print(f":: Estimated Cost (WaveNet Pricing): ${est_cost:.2f} USD")
    elif generated_files:
        playlist_path = os.path.join(output_dir, "playlist.m3u")
        with open(playlist_path, "w", encoding="utf-8") as f:
            for fname in generated_files:
                f.write(f"{fname}\n")
        print(f":: Playlist created: {playlist_path}")

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
    print(":: Transcription cache updated.")

    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    try:
        generate_audio()
    except KeyboardInterrupt:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
        print("\n:: Interrupted. Cache saved.")
