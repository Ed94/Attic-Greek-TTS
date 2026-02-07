import os
import re
import json
import tomli
import unicodedata
from google.cloud import texttospeech

with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google"]["credentials_path"]

def romanize_greek(text):
    """
    Turns Greek script into Latin script (e.g., Ὁμώνυμα -> Homonyma).
    This tricks the German voice engine into accepting the input inside the tag.
    """
    norm = unicodedata.normalize('NFD', text)
    result = []
    for char in norm:
        # Rough Breathing -> h
        if char == '\u0314':
            if result and result[-1].isalpha():
                 result.insert(-1, 'h')
            else:
                result.append('h')
            continue
            
        c = char.lower()
        # Basic mapping
        mapping = {
            'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z', 
            'η': 'e', 'θ': 'th', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm', 
            'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 
            'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'ph', 'χ': 'ch', 'ψ': 'ps', 
            'ω': 'o'
        }
        
        if c in mapping:
            result.append(mapping[c])
        elif 'a' <= c <= 'z': 
            result.append(char)
        elif char.isspace(): 
            result.append(char)
        
    return "".join(result)

def get_ipa_transcription(text, transcriber):
    """
    Generates IPA for a specific phrase.
    """
    if not text.strip():
        return ""
        
    try:
        words = text.split()
        ipa_words = []
        
        for word in words:
            raw_ipa = transcriber.transcribe(word)
            # Clean: Remove brackets and punctuation, but KEEP accents/diacritics
            clean_ipa = raw_ipa.replace("[", "").replace("]", "")
            clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
            # Remove internal spaces (fixes "ho" drop in CLTK)
            clean_ipa = clean_ipa.replace(" ", "")
            if clean_ipa:
                ipa_words.append(clean_ipa)
        
        return " ".join(ipa_words)

    except Exception as e:
        print(f"  [Notice] CLTK failed for '{text}' ({e}).")
        return ""

def build_ssml_with_pauses(full_text, transcriber):
    """
    Splits text by punctuation and builds SSML.
    Returns: (ssml_string, debug_list_of_dictionaries)
    """
    # Regex to capture punctuation
    parts = re.split(r'([,\.·;:\-])', full_text)
    
    ssml_parts    = ["<speak>"]
    debug_entries = []
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # Punctuation logic for rhythm/breaths
        if part in [',', ':']:
            ssml_parts.append('<break time="250ms"/>')
            debug_entries.append({"type": "break", "val": "250ms"})
        elif part in ['.', ';', '—']:
            ssml_parts.append('<break time="600ms"/>')
            debug_entries.append({"type": "break", "val": "600ms"})
        elif part in ['·', '-']:
            ssml_parts.append('<break time="400ms"/>')
            debug_entries.append({"type": "break", "val": "400ms"})
        else:
            # Text phrase
            ipa        = get_ipa_transcription(part, transcriber)
            dummy_text = romanize_greek(part)
            
            if ipa and dummy_text:
                ssml_parts.append(f'<phoneme alphabet="ipa" ph="{ ipa }">{ dummy_text }</phoneme>')
                debug_entries.append({
                    "type":      "word", 
                    "greek":     part, 
                    "romanized": dummy_text, 
                    "ipa":       ipa
                })
            else:
                ssml_parts.append(part)
                debug_entries.append({ "type": "fallback", "text": part })
                
    ssml_parts.append("</speak>")
    return "".join(ssml_parts), debug_entries

def parse_input_file(filepath):
    if not os.path.exists(filepath):
        print(f"Error: { filepath } not found.")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    raw_sections   = content.split(config["processing"]["delimiter"])
    clean_sections = []
    for section in raw_sections:
        clean_text = section.strip().replace("\n", " ")
        if clean_text:
            clean_sections.append(clean_text)
    return clean_sections

def generate_audio_directly():
    input_path = "input.txt"
    output_dir = "output"
    # Fallback to defaults if config keys missing
    voice_name = config["tts"].get("voice_name", "de-DE-Chirp3-HD-Kore")
    rate       = config["tts"].get("speaking_rate", 0.9)
    audio_enc  = config["tts"].get("audio_encoding", "MP3")
    
    # Initialize CLTK once
    from cltk.phonology.grc.transcription import Transcriber
    cltk_transcriber = Transcriber(
        dialect        = config["cltk"]["dialect"], 
        reconstruction = config["cltk"]["reconstruction"]
    )
    
    sections = parse_input_file(input_path)
    print(f"--- PROCESSING { len(sections) } SECTIONS ---")
    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok = True)
    print(f"Using Voice: {voice_name}")

    # For Logging
    full_debug_log = []
    for i, text in enumerate(sections):
        print(f"Generating Section {i+1}...")
        # Build SSML and get debug info
        ssml_text, section_debug = build_ssml_with_pauses(text, cltk_transcriber)
        # Store debug info
        clean_name    = re.sub(r'[^\w\s]', '', text[:20]).strip().replace(" ", "_")
        filename_base = f"{i+1:02d}_{clean_name}_{voice_name}_rate{rate}"
        full_debug_log.append({
            "filename":       f"{filename_base}.mp3",
            "original_text":  text,
            "generated_ssml": ssml_text,
            "ipa_breakdown":  section_debug
        })
        synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)
        # Parse language code from voice name (e.g., de-DE)
        lang_code    = "-".join(voice_name.split("-")[:2])
        voice        = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
        encoding_map = {
            "LINEAR16": texttospeech.AudioEncoding.LINEAR16, 
            "MP3":      texttospeech.AudioEncoding.MP3
        }
        audio_config = texttospeech.AudioConfig(
            audio_encoding =encoding_map.get(audio_enc, texttospeech.AudioEncoding.MP3),
            speaking_rate  = rate,
            pitch          = config["tts"]["pitch"]
        )
        output_path = os.path.join(output_dir, f"{filename_base}.mp3")

        try:
            response = client.synthesize_speech(
                request = texttospeech.SynthesizeSpeechRequest(
                    input        = synthesis_input, 
                    voice        = voice, 
                    audio_config = audio_config
                )
            )
            with open(output_path, "wb") as out:
                out.write(response.audio_content)
            print(f"  -> Saved: {output_path}")
        except Exception as e:
            print(f"  -> API Error: {e}")

    with open("debug_dump.json", "w", encoding="utf-8") as f:
        json.dump(full_debug_log, f, indent=2, ensure_ascii=False)
        print("\n--- Debug dump saved to debug_dump.json ---")

if __name__ == "__main__":
    generate_audio_directly()
