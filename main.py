"""
--------------------------------------------------------------------------------
SCRIPT: Ancient Greek TTS Generator (The "German Trojan Horse" Method)

WHAT THIS DOES:
    Generates MP3s of Ancient Greek text using Google Cloud's TTS API.

HOW IT WORKS:
    1. Pre-Processing: 
       - Converts Numerals (1, 26, 100) -> Greek Words (εἷς, εἴκοσι ἕξ, ἑκατόν).
       - Converts Roman Numerals (I, IV) -> Greek Words.
    2. Phonology (CLTK):
       - Uses the CLTK library to generate the IPA (Attic/Probert) for every word.
    3. "Trojan Horse" SSML:
       - Wraps the IPA in <phoneme> tags.
       - Uses "Romanized" dummy text as the content to trick the German voice.
    4. Stitching:
       - Breaks large text into chunks to satisfy Google's 5000-byte limit.
       - Stitches the returned audio bytes into a SINGLE output file.

DEPENDENCIES:
    - google-cloud-texttospeech
    - cltk (Must be installed and corpus available)
    - tomli
--------------------------------------------------------------------------------
"""

import os
import re
import json
import tomli
import unicodedata

# --- 1. IMPORTS ---
# We import CLTK at the top to make it clear it is the core phonology engine.
from cltk.phonology.grc.transcription import Transcriber
from google.cloud import texttospeech

# --- 2. CONFIG & SETUP ---
with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google"]["credentials_path"]

# Initialize CLTK globally or in main
# Ensure you have run: from cltk.data.fetch import FetchCorpus; FetchCorpus(language="grc").import_corpus("grc_models_cltk")
TRANSCRIBER = Transcriber(
    dialect=config["cltk"]["dialect"], 
    reconstruction=config["cltk"]["reconstruction"]
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
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20
}

LATIN_LETTERS = {
    "a": "ἄλφα", "b": "βῆτα", "c": "γάμμα", "d": "δέλτα", "e": "εἶ"
}

def number_to_greek(n):
    """ Converts an integer (0-999) to Ancient Greek phonetic words. """
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
        if n in GREEK_NUM_BASICS: # Exact 20
             words.append(GREEK_NUM_BASICS[n])
        elif n in GREEK_TENS: # Exact 30, 40...
            words.append(GREEK_TENS[n])
        else:
            tens = (n // 10) * 10
            units = n % 10
            if tens == 20: words.append(GREEK_NUM_BASICS[20])
            else: words.append(GREEK_TENS.get(tens, ""))
            if units > 0: words.append(GREEK_NUM_BASICS.get(units, ""))
    elif n > 0:
        words.append(GREEK_NUM_BASICS.get(n, ""))

    return " ".join([w for w in words if w])

def normalize_text_numerals(text):
    """ Replaces '26', 'IV', 'a' with Greek words. """
    def replace_match(match):
        token = match.group(0).lower()
        
        # Arabic (1, 26, 100)
        if token.isdigit():
            val = int(token)
            greek = number_to_greek(val)
            return f" {greek} " if greek else token
            
        # Roman (I, IV)
        if token in ROMAN_MAP:
            val = ROMAN_MAP[token]
            greek = number_to_greek(val)
            return f" {greek} " if greek else token
            
        # Latin Letters (a, b)
        if token in LATIN_LETTERS:
            return f" {LATIN_LETTERS[token]} "
            
        return token

    # Regex Replacements
    text = re.sub(r'\b([0-9]+)\b', replace_match, text)
    text = re.sub(r'\b([ivxIVX]+)\b', replace_match, text)
    text = re.sub(r'(?<=\d)[a-z]\b|\b[a-z]\b', replace_match, text) # Matches 'a' in '1a' or standalone 'a'
    return text

# --- 4. TEXT & PHONOLOGY HELPERS ---

def has_greek_chars(text):
    return bool(re.search(r'[\u0370-\u03FF\u1F00-\u1FFF]', text))

def sanitize_filename(text):
    text = re.sub(r'[\s\n\r]+', '_', text)
    text = re.sub(r'[^\w\-\u0370-\u03FF\u1F00-\u1FFF]', '', text)
    return text[:50].strip('_')

def romanize_greek(text):
    """ 
    Simple transliteration to Latin. 
    Required because the German voice engine rejects Greek characters in the input text,
    even if we provide IPA overrides.
    """
    norm = unicodedata.normalize('NFD', text)
    result = []
    for char in norm:
        if char == '\u0314': # Rough breathing -> h
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

def get_ipa_transcription(text):
    """
    Uses CLTK to generate IPA.
    """
    if not text.strip(): return ""
    try:
        words = text.split()
        ipa_words = []
        for word in words:
            # We only transcribe Greek words. Numerals have already been converted to Greek words.
            if not has_greek_chars(word): continue
            
            # --- CLTK CALL ---
            raw_ipa = TRANSCRIBER.transcribe(word)
            # -----------------
            
            # Cleanup for Google TTS compatibility
            clean_ipa = raw_ipa.replace("[", "").replace("]", "")
            clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
            clean_ipa = clean_ipa.replace(" ", "") # Remove internal syllable spaces
            if clean_ipa: ipa_words.append(clean_ipa)
            
        return " ".join(ipa_words)
    except Exception as e:
        print(f"  [Notice] CLTK failed for '{text}' ({e}).")
        return ""

def build_ssml_fragments(full_text):
    """
    Returns a list of SSML string fragments.
    """
    # 1. Normalize Numerals/Latin -> Greek Words
    full_text = normalize_text_numerals(full_text)
    
    # 2. Preserve Newlines for Pauses
    full_text = full_text.replace("\r\n", "\n")
    parts = re.split(r'([,\.·;:\-\n])', full_text)
    
    fragments = []
    debug_entries = []
    
    for raw_part in parts:
        if raw_part == '\n':
            fragments.append('<break time="80ms"/>')
            debug_entries.append({"type": "break", "val": "newline"})
            continue

        part = raw_part.strip()
        if not part: continue
            
        # Punctuation Pauses
        if part in [',', ':']:
            fragments.append('<break time="165ms"/>')
            continue
        elif part in ['.', ';', '—']:
            fragments.append('<break time="450ms"/>')
            continue
        elif part in ['·', '-']:
            fragments.append('<break time="300ms"/>')
            continue

        # Filter remaining non-Greek text (e.g. English headers)
        if not has_greek_chars(part):
            fragments.append('<break time="50ms"/>') 
            debug_entries.append({"type": "skip", "text": part})
            continue

        # Generate IPA using CLTK
        ipa = get_ipa_transcription(part)
        dummy_text = romanize_greek(part)
        
        if ipa and dummy_text:
            frag = f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>'
            fragments.append(frag)
            debug_entries.append({"type": "word", "greek": part, "ipa": ipa})
        else:
            debug_entries.append({ "type": "error", "text": part })

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
            request=texttospeech.SynthesizeSpeechRequest(
                input=synthesis_input, voice=voice_params, audio_config=audio_config
            )
        )
        return response.audio_content
    except Exception as e:
        print(f"    -> API Error: {e}")
        return None

def generate_audio_directly():
    input_path = config["files"].get("input_text", "input.txt")
    output_dir = config["tts"].get("output_dir", "output")
    
    voice_name = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Kore")
    rate       = config["tts"].get("speaking_rate", 0.9)
    audio_enc  = config["tts"].get("audio_encoding", "MP3")
    pitch      = config["tts"].get("pitch", 0.0)
    ext        = config["tts"].get("output_extension", "mp3")
    
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
        
        # 1. Build SSML (Includes Numeral Translation & CLTK IPA)
        fragments, section_debug = build_ssml_fragments(text)
        
        # 2. Chunking Logic (Stitching in Memory)
        MAX_BYTES = 4500 
        current_ssml_parts = ["<speak>"]
        current_length = len("<speak>")
        
        final_audio_bytes = bytearray()
        
        def flush_buffer(parts):
            parts.append("</speak>")
            chunk_str = "".join(parts)
            return fetch_audio_bytes(client, chunk_str, voice_params, audio_cfg)

        for frag in fragments:
            frag_len = len(frag.encode('utf-8'))
            
            if current_length + frag_len + len("</speak>") > MAX_BYTES:
                chunk_bytes = flush_buffer(current_ssml_parts)
                if chunk_bytes:
                    # Stitching: If WAV/LINEAR16, strip header from subsequent chunks. 
                    # If MP3, just concat.
                    if audio_enc == "LINEAR16" and len(final_audio_bytes) > 0:
                        final_audio_bytes += chunk_bytes[44:] 
                    else:
                        final_audio_bytes += chunk_bytes
                
                print(f"    -> Stitched chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")
                current_ssml_parts = ["<speak>"]
                current_length = len("<speak>")
            
            current_ssml_parts.append(frag)
            current_length += frag_len
        
        # Flush remaining
        if len(current_ssml_parts) > 1:
            chunk_bytes = flush_buffer(current_ssml_parts)
            if chunk_bytes:
                if audio_enc == "LINEAR16" and len(final_audio_bytes) > 0:
                    final_audio_bytes += chunk_bytes[44:]
                else:
                    final_audio_bytes += chunk_bytes
            print(f"    -> Stitched final chunk ({len(chunk_bytes) if chunk_bytes else 0} bytes)")

        # 3. Save Single File
        greek_slug = "".join([c for c in text[:40] if has_greek_chars(c) or c.isspace()])
        safe_slug = sanitize_filename(greek_slug)
        if not safe_slug: safe_slug = f"section_{i+1}"
        filename = f"{i+1:02d}_{safe_slug}_{voice_name}.{ext}"
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

    with open("debug_dump.json", "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)
        print("\n--- Debug dump saved to debug_dump.json ---")

if __name__ == "__main__":
    generate_audio_directly()
