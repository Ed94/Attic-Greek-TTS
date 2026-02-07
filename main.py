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

   To bypass the engine's language filter, we "romanize" the text inside the tag
   (e.g., writing 'logos' instead of 'λόγος'). The engine sees Latin letters and
   accepts the input, but the <phoneme> tag forces it to speak our custom IPA.

   1. INGEST & CLEAN
      - Load text.
      - Normalize Arabic (1, 2) and Roman (II, V) numerals into phonetic
        Ancient Greek words (εἷς, δύο) so they are spoken, not skipped.

   2. SEGMENTATION
      - Split text by structural delimiters (newlines, periods, colons).
      - This preserves the macro-rhythm of the text.

   3. PHONOLOGICAL ANALYSIS (Per Word)
      - Transcribe to IPA using CLTK (Attic/Probert).
      - NFD Normalize the IPA to isolate diacritics.
      - Calculate PITCH CONTOUR: Detect the exact index of the accent
        (Acute/Circumflex) and generate a dynamic SSML pitch curve
        (e.g., "Start Low -> Peak 20% at index 3 -> End Low").

   4. PACING & BREATH
      - Track word count.
      - If word count > Threshold, scan for "Trigger Words" (Prepositions/
        Conjunctions).
      - Insert a <break> tag BEFORE the trigger word to simulate a breath.

   5. CHUNKING & STITCHING
      - SSML expands text size significantly.
      - Break the stream into chunks < 4500 bytes (Google API limit).
      - Request audio for each chunk.
      - Strip WAV headers (if Linear16) and stitch bytes in memory.
      - Save a single, seamless MP3 file.

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

with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_cloud"]["service_account_file"]

# Initialize CLTK Engine
TRANSCRIBER = Transcriber(
    dialect        = config["cltk"]["dialect"], 
    reconstruction = config["cltk"]["reconstruction"]
)

# ==============================================================================
# 2. D A T A   M A P P I N G S
# ==============================================================================

# --- Breath Triggers (Prepositions, Conjunctions) ---
BREATH_TRIGGERS = {
    "καὶ", "ἀλλὰ", "ἢ", "ὅτι", "ἵνα", "ὡς", "ὥστε", "ἐπεὶ", "ἐπειδὴ",  # Conjunctions
    "εἰς", "πρὸς", "ἐν", "ἐπὶ", "περὶ", "παρὰ", "μετὰ", "διὰ", "ὑπὲρ", # Prepositions
    "ἀπὸ", "ἐκ", "ἐξ", "κατὰ", "ὑπὸ", "ὃς", "ἣ", "ὃ", "οἷος", "ὅσος"   # Relatives
}

# --- Numeral Dictionaries ---
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
# 3. N U M E R A L   &   T E X T   P R O C E S S I N G
# ==============================================================================

def number_to_greek(n):
    """ Converts integer 0-999 to Ancient Greek phonetic string. """
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
    """ Replaces '26', 'IV', 'a' with Greek words via Regex. """
    def replace_match(match):
        token = match.group(0).lower()
        if token.isdigit():     return f" {number_to_greek(int(token))} "
        if token in ROMAN_MAP:  return f" {number_to_greek(ROMAN_MAP[token])} "
        if token in LATIN_LETTERS: return f" {LATIN_LETTERS[token]} "
        return token

    text = re.sub(r'\b([0-9]+)\b',             replace_match, text)
    text = re.sub(r'\b([ivxIVX]+)\b',          replace_match, text)
    text = re.sub(r'(?<=\d)[a-z]\b|\b[a-z]\b', replace_match, text)
    return text

def romanize_greek(text):
    """ 
    Transliterates Greek to Latin to trick the German voice engine.
    Handles rough breathing (\u0314) -> 'h'.
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
# 4. P H O N O L O G Y   &   P I T C H   L O G I C
# ==============================================================================

def get_ipa_transcription(word):
    """ Generates NFD-Normalized IPA from CLTK. """
    if not word.strip(): return ""
    try:
        raw_ipa   = TRANSCRIBER.transcribe(word)
        norm_ipa  = unicodedata.normalize('NFD', raw_ipa)
        
        # Clean specific chars for SSML compatibility
        clean_ipa = norm_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "") 
        return clean_ipa
    except Exception:
        return ""

def calculate_pitch_contour(ipa_word):
    """
    Analyzes IPA to find accent position and returns SSML contour string.
    """
    if not ipa_word: return None
    
    # Load settings
    p_start = config.get("prosody", {}).get("contour_start", "-2%")
    p_peak  = config.get("prosody", {}).get("contour_peak",  "+20%")
    p_end   = config.get("prosody", {}).get("contour_end",   "-10%")
    
    # Accent Markers (Acute, Circumflex, Grave, Stress)
    accent_chars = ["\u0301", "\u0342", "\u0300", "\u0302", "\u0303", "\u02C8", "´", "ˆ", "`"]
    indices      = [i for i, char in enumerate(ipa_word) if char in accent_chars]
    
    if not indices: return None 

    idx       = indices[0]
    total_len = len(ipa_word)
    
    # Calculate Peak Position (10% to 90% of word duration)
    pos_ratio = max(0.1, min(0.9, idx / total_len))
    peak_pct  = int(pos_ratio * 100)
    
    return f"(0%,{p_start}) ({peak_pct}%,{p_peak}) (100%,{p_end})"

def is_breath_trigger(word):
    """ Checks if word is a natural spot to pause (preposition/conjunction). """
    # Strip punct/accents for comparison
    w = unicodedata.normalize('NFC', word.lower())
    # Simple check against set (ignoring accent variations for now)
    # Ideally we'd strip accents here, but exact match usually works for common words
    if w in BREATH_TRIGGERS: return True
    return False

# ==============================================================================
# 5. S S M L   C O N S T R U C T I O N
# ==============================================================================

def build_ssml_fragments(full_text):
    # 1. Normalize
    full_text = normalize_text_numerals(full_text)
    full_text = full_text.replace("\r\n", "\n")
    
    # 2. Handle Newlines (The "Empty Line" Logic)
    #    Replace Double Newlines with a temporary TOKEN
    #    Replace Single Newlines with SPACE to preserve flow
    token_dbl = "||DBL_BRK||"
    full_text = re.sub(r'\n\s*\n', token_dbl, full_text)
    full_text = full_text.replace("\n", " ")
    
    # 3. Config Loading
    rate         = config["tts"].get("speaking_rate", 0.9)
    pauses       = config.get("pauses", {})
    pacing       = config.get("pacing", {})
    
    def scale_time(time_str):
        if not time_str.endswith("ms"): return time_str
        try:
            val = int(time_str.replace("ms", ""))
            return f"{int(val / rate)}ms"
        except: return time_str

    t_breath     = scale_time(pauses.get("breath",  "250ms"))
    t_newline    = scale_time(pauses.get("newline", "80ms"))
    t_comma      = scale_time(pauses.get("comma",   "165ms"))
    t_period     = scale_time(pauses.get("period",  "450ms"))
    t_minor      = scale_time(pauses.get("minor",   "300ms"))
    
    max_breath   = pacing.get("max_breath_words", 9)
    force_breath = pacing.get("force_breath_words", 20)
    
    # 4. Split by Structure (Punctuation OR Double Break Token)
    #    We escape the pipes for the regex
    split_pattern = r'([,\.·;:\-]|\|\|DBL_BRK\|\|)'
    parts         = re.split(split_pattern, full_text)
    
    fragments     = []
    debug_entries = []
    
    words_since_pause = 0
    
    for raw_part in parts:
        
        # --- A. Handle Double Newlines ---
        if raw_part == token_dbl:
            fragments.append(f'<break time="{t_newline}"/>')
            debug_entries.append({"type": "break", "val": "newline"})
            words_since_pause = 0
            continue

        part = raw_part.strip()
        if not part: continue
            
        # --- B. Handle Punctuation ---
        if part in [',', ':', '.', ';', '—', '·', '-']:
            t = t_period
            if   part in [',', ':']: t = t_comma
            elif part in ['·', '-']: t = t_minor
            fragments.append(f'<break time="{t}"/>')
            words_since_pause = 0
            continue

        # --- C. Handle Words ---
        words = part.split()
        for i, word in enumerate(words):
            if not has_greek_chars(word): continue

            # Breath Logic
            words_since_pause += 1
            if words_since_pause > max_breath:
                if is_breath_trigger(word) or words_since_pause >= force_breath:
                    fragments.append(f'<break time="{t_breath}"/>')
                    debug_entries.append({"type": "breath", "trigger": word})
                    words_since_pause = 0
            
            # IPA & Contour
            ipa        = get_ipa_transcription(word)
            dummy_text = romanize_greek(word)
            
            if ipa and dummy_text:
                contour = calculate_pitch_contour(ipa)
                ph_tag  = f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>'
                frag    = f'<prosody contour="{contour}">{ph_tag}</prosody>' if contour else ph_tag
                
                fragments.append(frag)
                if i < len(words) - 1: fragments.append(" ") 
                
                debug_entries.append({
                    "type": "word", "greek": word, "ipa": ipa, "contour": contour
                })
            else:
                debug_entries.append({ "type": "error", "text": word })

    return fragments, debug_entries

# ==============================================================================
# 6. A U D I O   &   W A V   H E A D E R   F I X
# ==============================================================================

def fix_wav_header(wav_bytes):
    """
    Updates the ChunkSize and Subchunk2Size fields in the WAV header
    so players recognize the full length of the stitched file.
    """
    if len(wav_bytes) < 44: return wav_bytes
    
    total_size = len(wav_bytes)
    chunk_size = total_size - 8
    subchunk2_size = total_size - 44
    
    # WAV is Little Endian (<)
    # Bytes 4-8: ChunkSize (I = unsigned int 32-bit)
    new_header = wav_bytes[:4] + struct.pack('<I', chunk_size) + wav_bytes[8:40] + struct.pack('<I', subchunk2_size) + wav_bytes[44:]
    return new_header

def fetch_audio_bytes(client, ssml_chunk, voice_params, audio_config):
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml_chunk)
    try:
        response = client.synthesize_speech(
            request=texttospeech.SynthesizeSpeechRequest(
                input=synthesis_input, voice=voice_params, audio_config=audio_config
            )
        )
        return response.audio_content
    except Exception as e:
        print(f"    -> API Error: {e}")
        return None

def generate_audio_directly():
    
    # Config
    input_path = config["files"].get("input_text", "input.txt")
    output_dir = config["files"].get("output_dir", "output")
    debug_path = config["files"].get("debug_file", "debug_dump.json")
    
    voice_name = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Orus")
    rate       = config["tts"].get("speaking_rate", 0.9)
    audio_enc  = config["tts"].get("audio_encoding", "LINEAR16")
    pitch      = config["tts"].get("pitch", 0.0)
    ext        = config["tts"].get("output_extension", "wav")
    max_bytes  = config["processing"].get("max_chunk_bytes", 4500)
    
    if not os.path.exists(input_path): return
    with open(input_path, "r", encoding="utf-8") as f: content = f.read()
    sections = [s.strip() for s in content.split(config["processing"]["delimiter"]) if s.strip()]
    
    print(f"--- PROCESSING { len(sections) } SECTIONS ---")
    
    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok = True)
    
    lang_code = "-".join(voice_name.split("-")[:2])
    voice_params = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
    encoding_map = { "LINEAR16": texttospeech.AudioEncoding.LINEAR16, "MP3": texttospeech.AudioEncoding.MP3 }
    audio_cfg = texttospeech.AudioConfig(audio_encoding = encoding_map.get(audio_enc, texttospeech.AudioEncoding.LINEAR16), speaking_rate = rate, pitch = pitch)

    full_debug_log = []

    for i, text in enumerate(sections):
        print(f"Generating Section {i+1}...")
        
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
                    # If it's the very first chunk, keep header
                    if len(final_audio_bytes) == 0:
                        final_audio_bytes += chunk_bytes
                    else:
                        # Strip 44-byte WAV header from subsequent chunks
                        final_audio_bytes += chunk_bytes[44:]
                
                print(f"    -> Stitched chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")
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
                    final_audio_bytes += chunk_bytes[44:]
            print(f"    -> Stitched final chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")

        # --- FIX HEADER ---
        if audio_enc == "LINEAR16" and len(final_audio_bytes) > 44:
            final_audio_bytes = fix_wav_header(final_audio_bytes)

        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{i+1}"
        
        filename    = f"{i+1:02d}_{safe_slug}_{voice_name}_{str(rate)}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out: out.write(final_audio_bytes)
            print(f"  -> Saved Full File: {output_path}")
        else:
            print("  -> Error: No audio generated for this section.")

        full_debug_log.append({ "section": i+1, "original": text, "ipa_breakdown": section_debug })

    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)
    print(f"\n--- Debug dump saved to {debug_path} ---")

if __name__ == "__main__":
    generate_audio_directly()
