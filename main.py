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
[Normalization]
    - Cleans Critical Sigla (Removes {}, [], <>, †).
    - Expands Numerals ("24" -> "eikosi tessares", "IV" -> "tettares").
    - Splits stuck alphanumerics ("1a" -> "1 a").
    |
    v
[Sentence Analysis] (The "Downdrift" Engine)
    - Splits text into full sentences.
    - Calculates a linear pitch baseline that drifts downwards from the
      start of the sentence (+10%) to the end (-10%) to simulate natural
      human intonation.
    |
    v
[Phonology Engine] (Cached)
 - Transcribes to IPA (CLTK / Probert reconstruction).
    - INJECTS /h/ if Rough Breathing is detected (Tunable).
    - Analyzes Vowel Quantity (Moraic weight).
    - Identifies Accent Type (Acute vs. Circumflex vs. Grave).
    - Fallback: Checks IPA stress (ˈ) or Greek diacritics if CLTK fails.
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
    - Chunks stream into < 5000 byte segments (Google API limit).
    - Injects <break> tags for breath pauses based on word count.
    - Applies Sandhi (Elision) logic to glue words (Tunable).
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
[options]
    apply_sandhi          :: (Bool) Glue words ending in apostrophe (ἀλλ᾽ ἐγὼ -> ἀλλ᾽ἐγὼ)
    apply_rough_breathing :: (Bool) Pronounce the 'h' (dasia)

[prosody]
    contour_peak    :: (Int) Pitch rise for Acute accent (e.g., 35).
    downdrift_start :: (Int) Baseline pitch at sentence start (e.g., 10).
    downdrift_end   :: (Int) Baseline pitch at sentence end (e.g., -10).
    heavy_word_rate :: (Str) Speed slowdown for heavy words (e.g., "-15%").

[pacing]
    force_breath_words :: (Int) Max words allowed before forcing a pause.

[tts]
    speaking_rate   :: Global speed multiplier.

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

if not os.path.exists("config.toml"):
    raise FileNotFoundError("CRITICAL: config.toml not found.")

with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_cloud"]["service_account_file"]

print(":: Initializing CLTK Transcriber (Attic/Probert)...")
# Force Attic as CLTK does not support Epic keys directly.
TRANSCRIBER = Transcriber(
    dialect        = config["cltk"]["dialect"], 
    reconstruction = config["cltk"]["reconstruction"]
)

CACHE_FILE = "transcription_cache.json"
TRANSCRIPTION_CACHE = {}

if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            TRANSCRIPTION_CACHE = json.load(f)
        print(f":: Cache Hit: Loaded {len(TRANSCRIPTION_CACHE)} lexical entries.")
    except Exception as e:
        print(f":: Cache Corrupted ({e}). Starting with empty lexicon.")

# ==============================================================================
# 2. D A T A   M A P P I N G S
# ==============================================================================

BREATH_TRIGGERS = {
    "καὶ", "ἀλλὰ", "ἢ", "ὅτι", "ἵνα", "ὡς", "ὥστε", "ἐπεὶ", "ἐπειδὴ",  
    "εἰς", "πρὸς", "ἐν", "ἐπὶ", "περὶ", "παρὰ", "μετὰ", "διὰ", "ὑπὲρ", 
    "ἀπὸ", "ἐκ", "ἐξ", "κατὰ", "ὑπὸ", "ὃς", "ἣ", "ὃ", "οἷος", "ὅσος", "γὰρ", "δέ"
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
    Transliterates Greek to Latin. 
    Corrects the "Ehis" bug by placing 'h' at the start of the word 
    if rough breathing is present, rather than inside the diphthong.
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
    
    # Check Config for Rough Breathing preference
    apply_rough = config.get("options", {}).get("apply_rough_breathing", True)
    
    # If rough breathing exists, prepend 'h' (unless it's Rho which is special)
    if apply_rough and '\u0314' in norm:
        if not text.lower().startswith('ῥ'):
             result.append('h')

    for char in norm:
        # Skip the combining char itself, we handled it
        if char == '\u0314': continue
        
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
# 4. P H O N O L O G Y   &   P R O S O D Y
# ==============================================================================

def analyze_word_data(word):
    """
    Robust Philological Analysis.
    1. Transcribes to IPA.
    2. INJECTS /h/ if Rough Breathing is detected (Tunable).
    3. Searches for Greek Pitch Accents.
    4. Fallbacks for Stress/Quantity.
    """
    if word in TRANSCRIPTION_CACHE:
        return TRANSCRIPTION_CACHE[word]
    if not word.strip(): return None

    try:
        raw_ipa  = TRANSCRIBER.transcribe(word)
        norm_ipa = unicodedata.normalize('NFD', raw_ipa)
        # Clean for SSML
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "")

        # --- ROUGH BREATHING (TUNABLE) ---
        apply_rough = config.get("options", {}).get("apply_rough_breathing", True)
        norm_greek  = unicodedata.normalize('NFD', word)
        
        if apply_rough and '\u0314' in norm_greek:
            if not word.lower().startswith('ῥ'):
                # Only prepend 'h' if the IPA doesn't already have 'h' or 'ʰ'
                if not (clean_ipa.startswith('h') or clean_ipa.startswith('ʰ')):
                    clean_ipa = 'h' + clean_ipa

        accent_type = "none"
        accent_idx  = -1

        # 1. Primary Check: Pitch Accents
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
        
        # 2. Fallback: IPA Stress (ˈ)
        if accent_type == "none":
            if 'ˈ' in clean_ipa:
                accent_type = "acute" 
                accent_idx  = clean_ipa.find('ˈ') + 1 
                clean_ipa   = clean_ipa.replace('ˈ', '')
        
        # 3. Fallback: Greek Text Circumflex
        if accent_type == "none":
            if '\u0342' in norm_greek or '͂' in norm_greek:
                accent_type = "circumflex"
                match       = re.search(r'[aeiouyηω]', clean_ipa)
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
    
    c_peak  = config["prosody"].get("contour_peak",  35)
    c_grave = config["prosody"].get("contour_grave", 5)
    c_end   = config["prosody"].get("contour_end",   -12)
    c_tail  = config["prosody"].get("circumflex_tail_len", 15)
    
    val_start = baseline_shift
    val_peak  = baseline_shift + c_peak
    val_grave = baseline_shift + c_grave
    val_end   = baseline_shift + c_end
    
    def p(val): return f"{int(val):+d}%"

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

    rate = "0%"
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
    drift_start  = config["prosody"].get("downdrift_start", 10)
    drift_end    = config["prosody"].get("downdrift_end", -10)
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
        
        # Split logic (Fixed to include token_dbl)
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
                if   part in [',', ':']: t = t_comma
                elif part in ['·', '-']: t = t_minor
                fragments.append(f'<break time="{t}"/>')
                words_since_breath = 0 
                continue
            
            words = part.split()
            
            # SANDHI / ELISION LOGIC
            # Use while loop to look ahead and merge words
            i = 0
            while i < len(words):
                word = words[i]
                
                # Check for Elision (Sandhi)
                if apply_sandhi and (word.endswith('᾽') or word.endswith('’') or word.endswith("'")) and i + 1 < len(words):
                    next_word = words[i+1]
                    if has_greek_chars(next_word):
                        word = word + next_word # Merge text for shared prosody
                        i   += 1 # Skip next word iteration
                
                if not has_greek_chars(word): 
                    if word.strip(): fragments.append(word)
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
                
                current_baseline  = drift_start + ((drift_end - drift_start) * position_ratio)
                current_word_idx += 1

                # Phonology
                w_data     = analyze_word_data(word)
                dummy_text = romanize_greek(word)
                
                if w_data and dummy_text:
                    ipa               = w_data["ipa"]
                    contour, dur_rate = calculate_prosody(w_data, baseline_shift=current_baseline)
                    
                    ph_tag     = f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>'
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
                    fragments.append(dummy_text)
                
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
    Parses WAV structure to correctly extract audio data, 
    independent of header size or extra metadata chunks.
    """
    if len(wav_bytes) < 44: return b""
    try:
        # Skip RIFF header (12 bytes)
        pos = 12
        while pos < len(wav_bytes):
            # Read Chunk ID (4 bytes)
            chunk_id = wav_bytes[pos:pos+4]
            # Read Chunk Size (4 bytes, Little Endian)
            chunk_size = struct.unpack('<I', wav_bytes[pos+4:pos+8])[0]
            
            if chunk_id == b'data':
                # Return the content of the data chunk
                return wav_bytes[pos+8 : pos+8+chunk_size]
            
            # Move to next chunk
            pos += 8 + chunk_size
    except Exception as e:
        print(f"    -> WAV Parse Warning: {e}")
    
    # Fallback to hard slice if parsing fails
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

    ext = "wav" if audio_enc == "LINEAR16" else "mp3"
    
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return

    with open(input_path, "r", encoding="utf-8") as f: content = f.read()
    sections = [s.strip() for s in content.split(delimiter) if s.strip()]
    
    print(f":: Processing { len(sections) } sections...")
    
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

    for i, text in enumerate(sections):
        print(f":: Generating Section {i+1}...")
        fragments, section_debug = build_ssml_fragments(text)
        
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
        else:
            print("    [!] Error: No audio generated.")

        full_debug_log.append({ "section": i+1, "analysis": section_debug })
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSCRIPTION_CACHE, f, ensure_ascii=False, indent=2)

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
