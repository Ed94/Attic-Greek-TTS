"""
--------------------------------------------------------------------------------
SCRIPT: Ancient Greek TTS Generator (The "German Trojan Horse" Method)

WHAT THIS DOES:
    Generates MP3s of Ancient Greek text using Google Cloud's TTS API.

FEATURES:
    1. IPA Injection: Uses CLTK (Attic/Probert) to generate IPA.
    2. Numeral Translation: Converts Arabic (1, 26) -> Greek (εἷς, εἴκοσι ἕξ).
    3. High-Res Pitch Contouring: Normalizes IPA to detect accent positions 
       accurately and applies SSML pitch curves defined in config.toml.
    4. Configurable Pacing: Pauses for punctuation are now tunable.

FLOW:
    Text -> Normalize Numerals -> Split Punctuation -> Split Words -> 
    Generate IPA -> Detect Accent (NFD) -> Apply Contour -> 
    Stitch Bytes -> Save Single MP3.
--------------------------------------------------------------------------------
"""

import os
import re
import json
import tomli
import unicodedata

# --- 1. IMPORTS ---
from cltk.phonology.grc.transcription import Transcriber
from google.cloud import texttospeech

# --- 2. CONFIG & SETUP ---
with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google_cloud"]["service_account_file"]

# Initialize CLTK
TRANSCRIBER = Transcriber(
    dialect       = config["cltk"]["dialect"], 
    reconstruction= config["cltk"]["reconstruction"]
)

# --- 3. NUMERAL TRANSLATION LOGIC ---
GREEK_NUM_BASICS = {
    0: "μηδέν", 1: "εἷς", 2: "δύο", 3: "τρεῖς", 4: "τέτταρες",
    5: "πέντε", 6: "ἕξ", 7: "ἑπτά", 8: "ὀκτώ", 9: "ἐννέα", 10: "δέκα",
    11: "ἕνδεκα", 12: "δώδεκα", 13: "τρεῖς καὶ δέκα", 14: "τέτταρες καὶ δέκα",
    15: "πεντεκαίδεκα", 16: "ἑκκαίδεκα", 17: "ἑπτακαίδεκα", 18: "ὀκτωκαίδεκα", 
    19: "ἐννεακαίδεκα", 20: "εἴκοσι"
}
GREEK_TENS = {
    30: "τριάκοντα", 40: "τεσσαράκοντα", 50: "πεντήκοντα",
    60: "ἑξήκοντα", 70: "ἑβδομήκοντα", 80: "ὀγδοήκοντα", 90: "ἐνενήκοντα"
}
GREEK_HUNDREDS = {
    100: "ἑκατόν", 200: "διακόσιοι", 300: "τριακόσιοι", 400: "τετρακόσιοι",
    500: "πεντακόσιοι", 600: "ἑξακόσιοι", 700: "ἑπτακόσιοι", 800: "ὀκτακόσιοι",
    900: "ἐννακόσιοι"
}
ROMAN_MAP = {
    "i":  1,  "ii":  2,  "iii":  3,  "iv": 4, "v": 5, 
    "vi": 6,  "vii": 7,  "viii": 8,  "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xv":   15, "xx": 20
}
LATIN_LETTERS = {"a": "ἄλφα", "b": "βῆτα", "c": "γάμμα", "d": "δέλτα", "e": "εἶ"}

def number_to_greek(n):
    if n <= 20: return GREEK_NUM_BASICS.get(n, "")
    words = []
    if n >= 100:
        hundreds = (n // 100) * 100
        words.append(GREEK_HUNDREDS.get(hundreds, ""))
        n %= 100
        if n == 0: return " ".join(words)
    if n >= 20:
        if n in GREEK_NUM_BASICS: words.append(GREEK_NUM_BASICS[n])
        elif n in GREEK_TENS: words.append(GREEK_TENS[n])
        else:
            tens  = (n // 10) * 10
            units = n % 10
            if tens == 20: words.append(GREEK_NUM_BASICS[20])
            else: words.append(GREEK_TENS.get(tens, ""))
            if units > 0: words.append(GREEK_NUM_BASICS.get(units, ""))
    elif n > 0:
        words.append(GREEK_NUM_BASICS.get(n, ""))
    return " ".join([w for w in words if w])

def normalize_text_numerals(text):
    def replace_match(match):
        token = match.group(0).lower()
        if token.isdigit():        return f" {number_to_greek(int(token))} "
        if token in ROMAN_MAP:     return f" {number_to_greek(ROMAN_MAP[token])} "
        if token in LATIN_LETTERS: return f" {LATIN_LETTERS[token]} "
        return token
    text = re.sub(r'\b([0-9]+)\b', replace_match, text)
    text = re.sub(r'\b([ivxIVX]+)\b', replace_match, text)
    text = re.sub(r'(?<=\d)[a-z]\b|\b[a-z]\b', replace_match, text)
    return text

# --- 4. TEXT & PHONOLOGY HELPERS ---

def has_greek_chars(text):
    return bool(re.search(r'[\u0370-\u03FF\u1F00-\u1FFF]', text))

def sanitize_filename(text):
    text = re.sub(r'[\s\n\r]+', '_', text)
    text = re.sub(r'[^\w\-\u0370-\u03FF\u1F00-\u1FFF]', '', text)
    return text[:50].strip('_')

def romanize_greek(text):
    norm = unicodedata.normalize('NFD', text)
    result = []
    for char in norm:
        if char == '\u0314': 
            if result and result[-1].isalpha(): result.insert(-1, 'h')
            else: result.append('h')
            continue
        c = char.lower()
        mapping = {
            'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z', 
            'η': 'e', 'θ': 'th', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm', 
            'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 
            'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph', 'χ': 'ch', 'ψ': 'ps', 
            'ω': 'o'
        }
        if c in mapping: result.append(mapping[c])
        elif 'a' <= c <= 'z': result.append(char)
        elif char.isspace(): result.append(char)
    return "".join(result)

def get_ipa_transcription(word):
    if not word.strip(): return ""
    try:
        raw_ipa = TRANSCRIBER.transcribe(word)
        # NFD Normalize immediately so accents are separate characters
        raw_ipa = unicodedata.normalize('NFD', raw_ipa)
        
        clean_ipa = raw_ipa.replace("[", "").replace("]", "").replace("/", "")
        clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
        clean_ipa = clean_ipa.replace(" ", "") 
        return clean_ipa
    except Exception as e:
        return ""

def calculate_pitch_contour(ipa_word):
    """
    Analyzes NFD-Normalized IPA to find accent.
    Reads settings from config.toml [prosody].
    """
    if not ipa_word: return None
    
    # Load settings with defaults
    p_start = config.get("prosody", {}).get("contour_start", "-2%")
    p_peak  = config.get("prosody", {}).get("contour_peak", "+20%")
    p_end   = config.get("prosody", {}).get("contour_end", "-10%")
    
    # Accents: Acute(\u0301), Circumflex(\u0342), Vertical Stress(\u02C8)
    accent_indices = [i for i, char in enumerate(ipa_word) if char in ["\u0301", "\u0342", "\u02C8", "´", "ˆ"]]
    if not accent_indices:
        return None # No accent found

    idx       = accent_indices[0]
    total_len = len(ipa_word)
    
    # Calculate relative position (0.1 to 0.9)
    pos_ratio = max(0.1, min(0.9, idx / total_len))
    peak_pct  = int(pos_ratio * 100)
    
    # Dynamic Contour using config values
    return f"(0%,{p_start}) ({peak_pct}%,{p_peak}) (100%,{p_end})"

def build_ssml_fragments(full_text):
    full_text = normalize_text_numerals(full_text)
    full_text = full_text.replace("\r\n", "\n")
    
    # Load Pause Settings
    pauses    = config.get("pauses", {})
    t_comma   = pauses.get("comma", "165ms")
    t_period  = pauses.get("period", "450ms")
    t_minor   = pauses.get("minor", "300ms")
    t_newline = pauses.get("newline", "80ms")
    t_skip    = pauses.get("skip", "50ms")
    
    # Split by Punctuation
    parts = re.split(r'([,\.·;:\-\n])', full_text)
    
    fragments     = []
    debug_entries = []
    
    for raw_part in parts:
        # 1. Structural Delimiters
        if raw_part == '\n':
            fragments.append(f'<break time="{t_newline}"/>')
            debug_entries.append({"type": "break", "val": "newline"})
            continue

        part = raw_part.strip()
        if not part: continue
            
        if part in [',', ':']:
            fragments.append(f'<break time="{t_comma}"/>')
            continue
        elif part in ['.', ';', '—']:
            fragments.append(f'<break time="{t_period}"/>')
            continue
        elif part in ['·', '-']:
            fragments.append(f'<break time="{t_minor}"/>')
            continue

        # 2. Words
        words = part.split()
        for i, word in enumerate(words):
            if not has_greek_chars(word):
                continue

            ipa        = get_ipa_transcription(word)
            dummy_text = romanize_greek(word)
            
            if ipa and dummy_text:
                contour     = calculate_pitch_contour(ipa)
                phoneme_tag = f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>'
                
                if contour:
                    frag = f'<prosody contour="{contour}">{phoneme_tag}</prosody>'
                else:
                    frag = phoneme_tag

                fragments.append(frag)
                
                # Space separation between words
                if i < len(words) - 1:
                    fragments.append(" ") 

                debug_entries.append({"type": "word", "greek": word, "ipa": ipa, "contour": contour})
            else:
                debug_entries.append({ "type": "error", "text": word })

        # Logic check: If we just finished a block of words, and the NEXT part isn't punctuation,
        # we might want a tiny space. But usually re.split handles this via the delimiters.

    return fragments, debug_entries

# --- 5. AUDIO GENERATION ---

def parse_input_file(filepath):
    if not os.path.exists(filepath):
        print(f"Error: { filepath } not found.")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    raw_sections = content.split(config["processing"]["delimiter"])
    return [s.strip() for s in raw_sections if s.strip()]

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

def generate_audio_directly():
    input_path = config["files"].get("input_text", "input.txt")
    output_dir = config["files"].get("output_dir", "output")
    
    voice_name = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Kore")
    rate       = config["tts"].get("speaking_rate", 0.9)
    audio_enc  = config["tts"].get("audio_encoding", "MP3")
    pitch      = config["tts"].get("pitch", 0.0)
    ext        = config["tts"].get("output_extension", "mp3")
    max_bytes  = config["processing"].get("max_chunk_bytes", 4500)
    
    sections = parse_input_file(input_path)
    print(f"--- PROCESSING { len(sections) } SECTIONS ---")
    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok = True)
    
    lang_code = "-".join(voice_name.split("-")[:2])
    voice_params = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
    
    encoding_map = {
        "LINEAR16": texttospeech.AudioEncoding.LINEAR16, 
        "MP3":      texttospeech.AudioEncoding.MP3
    }
    
    audio_cfg = texttospeech.AudioConfig(
        audio_encoding = encoding_map.get(audio_enc, texttospeech.AudioEncoding.MP3),
        speaking_rate  = rate,
        pitch          = pitch
    )

    full_debug_log = []

    for i, text in enumerate(sections):
        print(f"Generating Section {i+1}...")
        
        fragments, section_debug = build_ssml_fragments(text)
        
        current_ssml_parts = ["<speak>"]
        current_length = len("<speak>")
        final_audio_bytes = bytearray()
        
        def flush_buffer(parts):
            parts.append("</speak>")
            chunk_str = "".join(parts)
            return fetch_audio_bytes(client, chunk_str, voice_params, audio_cfg)

        for frag in fragments:
            frag_len = len(frag.encode('utf-8'))
            if current_length + frag_len + len("</speak>") > max_bytes:
                chunk_bytes = flush_buffer(current_ssml_parts)
                if chunk_bytes:
                    if audio_enc == "LINEAR16" and len(final_audio_bytes) > 0:
                        final_audio_bytes += chunk_bytes[44:] 
                    else:
                        final_audio_bytes += chunk_bytes
                print(f"    -> Stitched chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")
                current_ssml_parts = ["<speak>"]
                current_length = len("<speak>")
            
            current_ssml_parts.append(frag)
            current_length += frag_len
        
        if len(current_ssml_parts) > 1:
            chunk_bytes = flush_buffer(current_ssml_parts)
            if chunk_bytes:
                if audio_enc == "LINEAR16" and len(final_audio_bytes) > 0:
                    final_audio_bytes += chunk_bytes[44:]
                else:
                    final_audio_bytes += chunk_bytes
            print(f"    -> Stitched final chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")

        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug  = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{i+1}"
        filename    = f"{i+1:02d}_{safe_slug}_{voice_name}.{ext}"
        output_path = os.path.join(output_dir, filename)

        if final_audio_bytes:
            with open(output_path, "wb") as out:
                out.write(final_audio_bytes)
            print(f"  -> Saved Full File: {output_path}")
        else:
            print("  -> Error: No audio generated for this section.")

        full_debug_log.append({
            "section": i+1,
            "original": text,
            "ipa_breakdown": section_debug
        })

    debug_path = config["files"].get("debug_file", "debug_dump.json")
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)
        print(f"\n--- Debug dump saved to {debug_path} ---")

if __name__ == "__main__":
    generate_audio_directly()
