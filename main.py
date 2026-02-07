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
[Normalization] -> (Expands "24" -> "tettaras kai eikosi", handles "IV")
    |
    v
[Tokenization]  -> (Splits by punctuation, preserving macro-rhythm)
    |
    v
[Phonology Engine] (Cached)
    - Transcribes to IPA (CLTK / Probert reconstruction).
    - Analyzes Vowel Quantity (Moraic weight).
    - Identifies Accent Type (Acute vs. Circumflex vs. Grave).
    |
    v
[Prosody Synthesizer]
    - Acute:      Rise (+35%) on target mora.
    - Circumflex: Rise-Fall (+25% -> -10%) on target syllable.
    - Grave:      Suppressed pitch (+5%).
    - Heavy Word: Time dilation (-15% speed) to simulate quantity.
    |
    v
[SSML Batcher]
    - Chunks stream into < 5000 byte segments (Google API limit).
    - Injects <break> tags for breath pauses based on word count.
    |
    v
[Audio Renderer]
    - Requests audio chunks from Google Cloud.
    - Binary Stitching:
        * WAV: Strips 44-byte RIFF headers from chunks 2..N.
        * MP3: Direct byte concatenation.
        * Re-writes File Size in WAV header (Little Endian).

TUNABLES (config.toml):
-----------------------
[prosody]
    contour_peak  :: How high the voice goes on an Acute accent.
                     Higher = more "sing-song" / musical.
    contour_start :: Baseline pitch entry.
    heavy_word_rate :: Speed slowdown for heavy words (e.g. "-15%")
[pacing]
    force_breath_words :: Max words allowed before forcing a pause.
                        Prevents the "run-on sentence" robot effect.
[tts]
    speaking_rate :: Global speed multiplier.

CACHING:
--------
IPA transcription is expensive. We maintain 'transcription_cache.json'.
The cache is updated atomically after every section is processed to prevent
data loss during long batch operations.

================================================================================
"""

import os
import re
import json
import struct
import tomli
import unicodedata
from   cltk.phonology.grc.transcription import Transcriber
from   google.cloud                     import texttospeech

# ==============================================================================
# 1. C O N F I G U R A T I O N   &   S E T U P
# ==============================================================================

# Load Configuration
if not os.path.exists("config.toml"):
    raise FileNotFoundError("config.toml not found.")

with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_cloud"]["service_account_file"]

# Initialize CLTK Engine
print("-> Initializing CLTK Transcriber (Attic/Probert)...")
TRANSCRIBER = Transcriber(
    dialect        = config["cltk"]["dialect"], 
    reconstruction = config["cltk"]["reconstruction"]
)

# Load Transcription Cache
CACHE_FILE = "transcription_cache.json"
TRANSCRIPTION_CACHE = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            TRANSCRIPTION_CACHE = json.load(f)
        print(f"-> Loaded {len(TRANSCRIPTION_CACHE)} words from cache.")
    except:
        print("-> Cache file corrupted or empty. Starting fresh.")

# ==============================================================================
#   2.  D A T A   T A B L E S
# ==============================================================================

# Words that naturally trigger a pause (Prepositions/Conjunctions/Particles)
BREATH_TRIGGERS = {
    "καὶ", "ἀλλὰ", "ἢ", "ὅτι", "ἵνα", "ὡς", "ὥστε", "ἐπεὶ", "ἐπειδὴ",  
    "εἰς", "πρὸς", "ἐν", "ἐπὶ", "περὶ", "παρὰ", "μετὰ", "διὰ", "ὑπὲρ", 
    "ἀπὸ", "ἐκ", "ἐξ", "κατὰ", "ὑπὸ", "ὃς", "ἣ", "ὃ", "οἷος", "ὅσος", "γὰρ", "δέ"
}

# Numeral Expansion Tables
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
#   3.  T E X T   N O R M A L I Z A T I O N
# ==============================================================================

def number_to_greek(n):
    """ Recursive integer expansion to Attic Greek text. """
    if n <= 20: return GREEK_NUM_BASICS.get(n, "")
    words = []
    
    # Hundreds
    if n >= 100:
        hundreds = (n // 100) * 100
        words.append(GREEK_HUNDREDS.get(hundreds, ""))
        n %= 100
        if n == 0: return " ".join(words)

    # Tens & Units
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
    """ Regex pass to catch '24', 'IV', 'a' and convert to phonetic Greek words. """
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
    Transliterates Greek to Latin characters.
    This is the "skin" of the Trojan Horse. The TTS engine sees this text
    and accepts it as valid input, while the <phoneme> tag overrides the audio.
    
    CRITICAL: Handles 'Rough Breathing' (Spiritus Asper).
    """
    norm = unicodedata.normalize('NFD', text)
    result = []
    
    mapping = {
        'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z', 
        'η': 'e', 'θ': 'th','ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm', 
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 
        'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph','χ': 'ch','ψ': 'ps', 
        'ω': 'o'
    }

    for char in norm:
        # Detect Spiritus Asper (\u0314) -> Inject 'h'
        if char == '\u0314': 
            if result and result[-1].isalpha(): result.insert(-1, 'h')
            else: result.append('h')
            continue
        
        c = char.lower()
        if   c in mapping:    result.append(mapping[c])
        elif 'a' <= c <= 'z': result.append(char)
        elif char.isspace():  result.append(char)
        
    return "".join(result)

def has_greek_chars(text):
    return bool(re.search(r'[\u0370-\u03FF\u1F00-\u1FFF]', text))

def sanitize_filename(text):
    text = re.sub(r'[\s\n\r]+', '_', text)
    text = re.sub(r'[^\w\-\u0370-\u03FF\u1F00-\u1FFF]', '', text)
    return text[:50].strip('_')

# ==============================================================================
# 4. P H O N O L O G Y   &   C A C H I N G
# ==============================================================================

def analyze_word_data(word):
    """
    The Brain.
    1. Checks Cache.
    2. Transcribes via CLTK.
    3. Identifies Accent Type (Acute/Circumflex/Grave).
    4. Identifies Quantity (Heavy vs Light).
    """
    if word in TRANSCRIPTION_CACHE:
        return TRANSCRIPTION_CACHE[word]
    
    if not word.strip(): return None

    try:
        raw_ipa  = TRANSCRIBER.transcribe(word)
        norm_ipa = unicodedata.normalize('NFD', raw_ipa)
        
        # Strip IPA brackets and non-speech markers for SSML safety
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "")

        # --- SCHOLARLY ANALYSIS ---
        
        # 1. Detect Accent Type & Position
        # Acute: \u0301 (´), Circumflex: \u0342 (ˆ), Grave: \u0300 (`)
        accent_type = "none"
        accent_idx  = -1
        
        for i, char in enumerate(clean_ipa):
            if char == "\u0342" or char == "ˆ":
                accent_type = "circumflex"
                accent_idx  = i
                break
            elif char == "\u0301" or char == "´":
                accent_type = "acute"
                accent_idx  = i
                break
            elif char == "\u0300" or char == "`":
                accent_type = "grave"
                accent_idx  = i
                break
        
        # 2. Detect Quantity (Moraic Weight)
        # We look for the IPA length marker (ː) or long vowels (η, ω).
        long_markers = clean_ipa.count('ː')
        has_long_vowels = bool(re.search(r'[ηω]', word))
        is_heavy = long_markers > 0 or has_long_vowels

        data = {
            "raw_ipa":     raw_ipa,
            "ipa":         clean_ipa,
            "accent_type": accent_type,
            "accent_idx":  accent_idx,
            "is_heavy":    is_heavy,
            "len":         len(clean_ipa)
        }
        
        TRANSCRIPTION_CACHE[word] = data
        return data

    except Exception as e:
        # Silently fail on transcription errors to allow fallback to raw text
        return None

def calculate_prosody(word_data):
    """
    Converts phonological data into SSML <prosody> parameters.
    Returns: (contour_string, rate_string)
    """
    if not word_data: return None, "0%"
    
    ipa    = word_data["ipa"]
    a_type = word_data["accent_type"]
    idx    = word_data["accent_idx"]
    total  = word_data["len"]
    
    # --- PITCH CONTOUR (The Melody) ---
    # Load Tunables from Config
    p_start   = config["prosody"].get("contour_start", "+0%")
    p_peak    = config["prosody"].get("contour_peak",  "+35%")
    p_grave   = config["prosody"].get("contour_grave", "+5%")
    p_end     = config["prosody"].get("contour_end",   "-12%")
    
    # How long (in %) does the circumflex fall take?
    circ_tail = config["prosody"].get("circumflex_tail_len", 15) 
    
    contour = None
    if idx >= 0 and total > 0:
        # Calculate where the accent falls as a percentage of the word
        pos_ratio = max(0.1, min(0.9, idx / total))
        peak_pct  = int(pos_ratio * 100)
        
        if a_type == "circumflex":
            # Perispomenon: Rise and Fall on the same syllable.
            tail_pct = min(peak_pct + circ_tail, 100)
            contour = f"(0%,{p_start}) ({peak_pct}%,{p_peak}) ({tail_pct}%,{p_end}) (100%,{p_end})"
        
        elif a_type == "grave":
            # Barytone in context: Pitch is suppressed.
            contour = f"(0%,{p_start}) ({peak_pct}%,{p_grave}) (100%,{p_end})"
            
        else: 
            # Oxytones/Paroxytones (Acute): Sharp rise to peak, then fall.
            contour = f"(0%,{p_start}) ({peak_pct}%,{p_peak}) (100%,{p_end})"

    # --- DURATION (The Rhythm) ---
    rate = "0%"
    # Slow down heavy words by configured amount (default -15%)
    if word_data["is_heavy"]:
        rate = config["prosody"].get("heavy_word_rate", "-15%")

    return contour, rate

def is_breath_trigger(word):
    w = unicodedata.normalize('NFC', word.lower())
    return w in BREATH_TRIGGERS

# ==============================================================================
# 5. S S M L   C O N S T R U C T I O N
# ==============================================================================

def build_ssml_fragments(full_text):
    """
    Parses text into a list of SSML strings.
    Handles:
    - Punctuation Pauses
    - Breath insertion (Logic based on word count)
    - Phoneme injection
    """
    
    # 1. Clean & Normalize
    full_text = normalize_text_numerals(full_text)
    full_text = full_text.replace("\r\n", "\n")
    
    # 2. Tokenize Newlines (Double newline = Paragraph break)
    token_dbl = "||DBL_BRK||"
    full_text = re.sub(r'\n\s*\n', token_dbl, full_text)
    full_text = full_text.replace("\n", " ")
    
    # 3. Load Timing Config
    rate         = config["tts"].get("speaking_rate", 1.0)
    pauses       = config.get("pauses", {})
    pacing       = config.get("pacing", {})
    
    # Helper to scale ms values by speaking rate
    def scale_time(time_str):
        if not time_str.endswith("ms"): return time_str
        try:
            val = int(time_str.replace("ms", ""))
            return f"{int(val / rate)}ms"
        except: return time_str

    t_breath     = scale_time(pauses.get("breath",  "145ms"))
    t_newline    = scale_time(pauses.get("newline", "180ms"))
    t_comma      = scale_time(pauses.get("comma",   "80ms"))
    t_period     = scale_time(pauses.get("period",  "145ms"))
    t_minor      = scale_time(pauses.get("minor",   "215ms"))
    
    max_breath   = pacing.get("max_breath_words", 9)
    force_breath = pacing.get("force_breath_words", 20)
    
    # 4. Split by logical delimiters
    split_pattern = r'([,\.·;:\-]|\|\|DBL_BRK\|\|)'
    parts         = re.split(split_pattern, full_text)
    
    fragments     = []
    debug_entries = []
    
    words_since_pause = 0
    
    for raw_part in parts:
        # Case: Paragraph Break
        if raw_part == token_dbl:
            fragments.append(f'<break time="{t_newline}"/>')
            words_since_pause = 0
            # Log break for debug alignment
            debug_entries.append({"type": "break", "kind": "newline"})
            continue

        part = raw_part.strip()
        if not part: continue
            
        # Case: Punctuation
        if part in [',', ':', '.', ';', '—', '·', '-']:
            t = t_period
            if   part in [',', ':']: t = t_comma
            elif part in ['·', '-']: t = t_minor
            fragments.append(f'<break time="{t}"/>')
            words_since_pause = 0
            debug_entries.append({"type": "break", "kind": part})
            continue

        # Case: Words
        words = part.split()
        for i, word in enumerate(words):
            if not has_greek_chars(word): continue

            # Breath Logic: Insert pause if too many words have passed
            words_since_pause += 1
            if words_since_pause > max_breath:
                if is_breath_trigger(word) or words_since_pause >= force_breath:
                    fragments.append(f'<break time="{t_breath}"/>')
                    words_since_pause = 0
                    debug_entries.append({"type": "breath", "trigger": word})
            
            # Phonological Assembly
            w_data = analyze_word_data(word)
            dummy_text = romanize_greek(word)
            
            if w_data and dummy_text:
                ipa               = w_data["ipa"]
                contour, dur_rate = calculate_prosody(w_data)
                
                # 1. Base Phoneme
                ph_tag     = f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>'
                final_ssml = ph_tag
                
                # 2. Apply Quantity (Duration)
                if dur_rate != "0%":
                    final_ssml = f'<prosody rate="{dur_rate}">{final_ssml}</prosody>'

                # 3. Apply Accent (Pitch)
                if contour:
                    final_ssml = f'<prosody contour="{contour}">{final_ssml}</prosody>'
                
                fragments.append(final_ssml)
                
                # Rich Debug Logging
                debug_entries.append({
                    "type":      "word",
                    "greek":      word, 
                    "romanized": dummy_text,
                    "phonology": {
                        "ipa_raw":   w_data.get("raw_ipa", ""),
                        "ipa_clean": ipa,
                        "accent":    w_data["accent_type"],
                        "heavy":     w_data["is_heavy"],
                    },
                    "ssml_actions": {
                        "contour": contour,
                        "rate":    dur_rate,
                        "tag":     final_ssml
                    }
                })

                # Space between words
                if i < len(words) - 1: fragments.append(" ") 
                
            else:
                # Fallback: Just speak the romanized text (better than silence)
                fragments.append(dummy_text)
                if i < len(words) - 1: fragments.append(" ")
                debug_entries.append({"type": "fallback", "text": word})

    return fragments, debug_entries

# ==============================================================================
# 6. A U D I O   H A N D L I N G
# ==============================================================================

def fix_wav_header(wav_bytes):
    """ Updates ChunkSize and Subchunk2Size in WAV header. """
    if len(wav_bytes) < 44: return wav_bytes
    total_size     = len(wav_bytes)
    chunk_size     = total_size - 8
    subchunk2_size = total_size - 44
    new_header     = wav_bytes[:4] + struct.pack('<I', chunk_size) + wav_bytes[8:40] + struct.pack('<I', subchunk2_size) + wav_bytes[44:]
    return new_header

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

    # Format logic
    ext = "wav" if audio_enc == "LINEAR16" else "mp3"
    
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return

    with open(input_path, "r", encoding="utf-8") as f: content = f.read()
    
    # Split text by delimiter for multi-file generation
    sections = [s.strip() for s in content.split(delimiter) if s.strip()]
    
    print(f":: Processing { len(sections) } sections...")
    
    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok = True)
    
    # Configure Voice
    lang_code    = "-".join(voice_name.split("-")[:2])
    voice_params = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
    
    # Configure Audio
    encoding_enum = texttospeech.AudioEncoding.LINEAR16
    if audio_enc == "MP3": encoding_enum = texttospeech.AudioEncoding.MP3
    
    audio_cfg = texttospeech.AudioConfig(
        audio_encoding = encoding_enum, 
        speaking_rate  = rate, 
        pitch          = pitch_val
    )

    full_debug_log = []

    for i, text in enumerate(sections):
        print(f":: Generating Section {i+1}...")
        
        fragments, section_debug = build_ssml_fragments(text)
        
        current_ssml_parts = ["<speak>"]
        current_length     = len("<speak>")
        final_audio_bytes  = bytearray()
        
        def flush_buffer(parts):
            parts.append("</speak>")
            return fetch_audio_bytes(client, "".join(parts), voice_params, audio_cfg)

        # --- CHUNK LOOP ---
        for frag in fragments:
            frag_len = len(frag.encode('utf-8'))
            
            # Check size limit (Google limit is 5000 bytes)
            if current_length + frag_len + len("</speak>") > max_bytes:
                chunk_bytes = flush_buffer(current_ssml_parts)
                if chunk_bytes:
                    if len(final_audio_bytes) == 0:
                        final_audio_bytes += chunk_bytes
                    else:
                        # STITCHING LOGIC
                        if audio_enc == "LINEAR16":
                            # Strip 44 byte header from subsequent chunks
                            final_audio_bytes += chunk_bytes[44:]
                        else:
                            # MP3 - Just append
                            final_audio_bytes += chunk_bytes
                
                print(f"    -> Stitched chunk.")
                current_ssml_parts = ["<speak>"]
                current_length     = len("<speak>")
            
            current_ssml_parts.append(frag)
            current_length += frag_len
        
        # --- FLUSH REMAINING ---
        if len(current_ssml_parts) > 1:
            chunk_bytes = flush_buffer(current_ssml_parts)
            if chunk_bytes:
                if len(final_audio_bytes) == 0:
                    final_audio_bytes += chunk_bytes
                else:
                    if audio_enc == "LINEAR16":
                        final_audio_bytes += chunk_bytes[44:]
                    else:
                        final_audio_bytes += chunk_bytes

        # --- FIX HEADER (WAV ONLY) ---
        if audio_enc == "LINEAR16" and len(final_audio_bytes) > 44:
            final_audio_bytes = fix_wav_header(final_audio_bytes)

        # --- SAVE AUDIO ---
        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{i+1}"
        
        filename    = f"{i+1:02d}_{safe_slug}_{voice_name}_{str(rate)}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out: out.write(final_audio_bytes)
            print(f"    -> Saved: {output_path}")
        else:
            print("    [!] Error: No audio generated.")

        full_debug_log.append({ "section": i+1, "analysis": section_debug })

        # --- SAVE CACHE PER SECTION ---
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
        print(f"    -> Cache saved ({len(TRANSCRIPTION_CACHE)} entries).")

    # Save Cache Final (Redundant but safe)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
    print(":: Transcription cache updated.")

    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    try:
        generate_audio()
    except KeyboardInterrupt:
        # Save cache on exit even if interrupted
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)
        print("\n:: Interrupted. Cache saved.")
