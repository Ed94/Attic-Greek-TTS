import os
import sys
import json
import re
import tomli
import unicodedata
from google.cloud import texttospeech

# --- 1. CONFIG LOADER ---
with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google"]["credentials_path"]

# --- 2. HELPERS ---

def romanize_greek(text):
    """
    Turns Greek script into Latin script (e.g., Ὁμώνυμα -> Homonyma).
    This tricks the German voice engine into accepting the input.
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
        if c == 'α': result.append('a')
        elif c == 'β': result.append('b')
        elif c == 'γ': result.append('g')
        elif c == 'δ': result.append('d')
        elif c == 'ε': result.append('e')
        elif c == 'ζ': result.append('z')
        elif c == 'η': result.append('e')
        elif c == 'θ': result.append('th')
        elif c == 'ι': result.append('i')
        elif c == 'κ': result.append('k')
        elif c == 'λ': result.append('l')
        elif c == 'μ': result.append('m')
        elif c == 'ν': result.append('n')
        elif c == 'ξ': result.append('x')
        elif c == 'ο': result.append('o')
        elif c == 'π': result.append('p')
        elif c == 'ρ': result.append('r')
        elif c == 'σ': result.append('s')
        elif c == 'ς': result.append('s')
        elif c == 'τ': result.append('t')
        elif c == 'υ': result.append('y')
        elif c == 'φ': result.append('ph')
        elif c == 'χ': result.append('ch')
        elif c == 'ψ': result.append('ps')
        elif c == 'ω': result.append('o')
        elif 'a' <= c <= 'z': result.append(char)
        elif char.isspace(): result.append(char)
        
    return "".join(result)

def get_ipa_transcription(text):
    """
    Generates IPA for a specific phrase, removing internal spaces to fix "h" dropping,
    but keeping flow clean.
    """
    if not text.strip():
        return ""
        
    try:
        from cltk.phonology.grc.transcription import Transcriber
        cltk_transcriber = Transcriber(dialect="Attic", reconstruction="Probert")
        
        words = text.split()
        ipa_words = []
        
        for word in words:
            raw_ipa = cltk_transcriber.transcribe(word)
            # Clean: Remove brackets and punctuation
            clean_ipa = raw_ipa.replace("[", "").replace("]", "")
            clean_ipa = re.sub(r'[,\.·;:\-—’]', '', clean_ipa)
            # CRITICAL: Remove spaces INSIDE the word (fixes "ho" drop)
            clean_ipa = clean_ipa.replace(" ", "")
            if clean_ipa:
                ipa_words.append(clean_ipa)
        
        return " ".join(ipa_words)

    except Exception as e:
        print(f"  [Notice] CLTK failed ({e}). Returning empty.")
        return ""

def build_ssml_with_pauses(full_text):
    """
    Splits text by punctuation and builds SSML with explicit <break> tags.
    """
    # Regex to capture punctuation: . , · ; (question mark)
    # We split but keep the delimiters (punctuation)
    parts = re.split(r'([,\.·;:\-])', full_text)
    
    ssml_parts = ["<speak>"]
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # Check if it is punctuation
        if part in [',', ':']:
            ssml_parts.append('<break time="250ms"/>')
        elif part in ['.', ';', '—']:
            ssml_parts.append('<break time="600ms"/>')
        elif part in ['·', '-']:
            ssml_parts.append('<break time="400ms"/>')
        else:
            # It's a text phrase. Generate IPA and wrap in phoneme.
            ipa = get_ipa_transcription(part)
            dummy_text = romanize_greek(part)
            
            if ipa and dummy_text:
                ssml_parts.append(f'<phoneme alphabet="ipa" ph="{ipa}">{dummy_text}</phoneme>')
            else:
                # Fallback if something fails
                ssml_parts.append(part)
                
    ssml_parts.append("</speak>")
    return "".join(ssml_parts)

# --- 3. TEXT PARSER ---
def parse_input_file(filepath):
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    raw_sections = content.split("---")
    clean_sections = []
    for section in raw_sections:
        clean_text = section.strip().replace("\n", " ")
        if clean_text:
            clean_sections.append(clean_text)
    return clean_sections

# --- 4. GENERATION ---
def generate_audio_directly():
    """
    We skip the JSON staging file for IPA caching because we are now
    generating IPA dynamically per-phrase to handle pauses correctly.
    """
    input_path = config["files"]["input_text"]
    output_dir = config["tts"]["output_dir"]
    voice_name = config["tts"]["voice_name"]
    rate = config["tts"]["speaking_rate"]
    
    sections = parse_input_file(input_path)
    print(f"--- PROCESSING {len(sections)} SECTIONS ---")

    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok=True)
    print(f"Using Voice: {voice_name}")

    for i, text in enumerate(sections):
        print(f"Generating Section {i+1}...")
        
        # Build the complex SSML with breaks
        ssml_text = build_ssml_with_pauses(text)
        
        synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)

        lang_code = "-".join(voice_name.split("-")[:2])
        voice = texttospeech.VoiceSelectionParams(language_code=lang_code, name=voice_name)
        
        encoding_map = {"LINEAR16": texttospeech.AudioEncoding.LINEAR16, "MP3": texttospeech.AudioEncoding.MP3}
        chosen_encoding = config["tts"].get("audio_encoding", "MP3")
        audio_config = texttospeech.AudioConfig(
            audio_encoding=encoding_map.get(chosen_encoding, texttospeech.AudioEncoding.MP3),
            speaking_rate=rate
        )

        clean_name = re.sub(r'[^\w\s]', '', text[:20]).strip().replace(" ", "_")
        ext = config["tts"]["output_extension"]
        filename = f"{i+1:02d}_{clean_name}_{voice_name}_rate{rate}.{ext}"
        output_path = os.path.join(output_dir, filename)

        try:
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            with open(output_path, "wb") as out:
                out.write(response.audio_content)
            print(f"  -> Saved: {filename}")
        except Exception as e:
            print(f"  -> API Error: {e}")

if __name__ == "__main__":
    # We no longer use prepare_staging_file because the logic is dynamic now
    generate_audio_directly()
