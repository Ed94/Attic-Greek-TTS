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

# --- 2. FALLBACK TRANSCRIBER (Regex Based) ---
# Used if CLTK fails to load or crashes
class FallbackTranscriber:
    def __init__(self):
        self.map = {
            'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'zd', 
            'η': 'ɛː', 'θ': 'tʰ', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm', 
            'ν': 'n', 'ξ': 'ks', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 
            'ς': 's', 'τ': 't', 'υ': 'y', 'φ': 'pʰ', 'χ': 'kʰ', 'ψ': 'ps', 
            'ω': 'ɔː', 
            'OU': 'uː', 'EI': 'eː', 'AI': 'ai', 'OI': 'oi', 'YI': 'yi',
            'AY': 'au', 'EY': 'eu'
        }

    def transcribe(self, text):
        norm = unicodedata.normalize('NFD', text)
        output = []
        skip_next = False
        for i, char in enumerate(norm):
            if skip_next: skip_next = False; continue
            
            # Punctuation to space
            if char in [',', '.', '·', ';', ':', '—', '-', '’']: output.append(" "); continue
            # Breathings/Accents
            if char == '\u0314': 
                if output: output.insert(-1, 'h')
                continue
            if char in ['\u0301', '\u0342']: 
                if output: output.insert(-1, 'ˈ')
                continue
            if char in ['\u0313', '\u0300', '\u0345']: continue 
            
            base_char = char.lower()
            next_char = norm[i+1] if i+1 < len(norm) else ""
            
            # Simple Diphthongs
            if base_char == 'ο' and next_char.lower() == 'υ': output.append("uː"); skip_next = True; continue
            if base_char == 'ε' and next_char.lower() == 'ι': output.append("eː"); skip_next = True; continue
            if base_char == 'α' and next_char.lower() == 'ι': output.append("ai"); skip_next = True; continue
            if base_char == 'ο' and next_char.lower() == 'ι': output.append("oi"); skip_next = True; continue
                
            if base_char in self.map: output.append(self.map[base_char])
            elif char.isspace(): output.append(" ")
        return "".join(output)

# --- 3. MASTER TRANSCRIBER WRAPPER ---
def get_ipa_transcription(text):
    """
    Tries to use CLTK. If it fails (missing data), falls back to regex.
    """
    # Try importing CLTK inside the function to catch setup errors
    try:
        from cltk.phonology.grc.transcription import Transcriber
        
        # --- FIXED: Capitalized Arguments based on your docs ---
        cltk_transcriber = Transcriber(dialect="Attic", reconstruction="Probert")
        
        # CLTK returns IPA with punctuation. We must strip it.
        raw_ipa = cltk_transcriber.transcribe(text)
        
        # Clean up CLTK output (remove brackets, punctuation)
        clean_ipa = raw_ipa.replace("[", "").replace("]", "")
        
        # Strip punctuation symbols from the IPA string
        clean_ipa = re.sub(r'[,\.·;:\-—’]', ' ', clean_ipa)
        
        # Normalize spaces
        return re.sub(r'\s+', ' ', clean_ipa).strip()
        
    except Exception as e:
        print(f"  [Notice] CLTK failed ({e}). Using Fallback Transcriber.")
        fb = FallbackTranscriber()
        return fb.transcribe(text)

# --- 4. TEXT PARSER ---
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

# --- 5. PREPARATION ---
def prepare_staging_file():
    input_path = config["files"]["input_text"]
    staging_path = config["files"]["intermediate_data"]
    sections = parse_input_file(input_path)
    print(f"--- ANALYZING {len(sections)} SECTIONS ---")

    work_list = []
    
    for i, text in enumerate(sections):
        preview = (text[:50] + '...') if len(text) > 50 else text
        print(f"Processing Section {i+1}: {preview}")
        
        # Calls the smart wrapper
        ipa = get_ipa_transcription(text)
        
        work_list.append({"id": i + 1, "text": text, "ipa": ipa, "status": "ready"})

    with open(staging_path, "w", encoding="utf-8") as f:
        json.dump(work_list, f, indent=4, ensure_ascii=False)
    print(f"Created '{staging_path}'.")

# --- 6. GENERATION ---
def generate_audio_from_staging():
    staging_path = config["files"]["intermediate_data"]
    output_dir = config["tts"]["output_dir"]
    voice_name = config["tts"]["voice_name"]
    rate = config["tts"]["speaking_rate"]
    
    if not os.path.exists(staging_path):
        prepare_staging_file()
        return

    with open(staging_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    client = texttospeech.TextToSpeechClient()
    os.makedirs(output_dir, exist_ok=True)
    print(f"Using Voice: {voice_name}")

    for item in data:
        text = item["text"]
        ipa = item.get("ipa", "")
        
        if ipa:
            # Strip punctuation from display text to prevent "Comma" reading
            clean_display_text = re.sub(r'[,\.·;:\-—’]', '', text)
            
            ssml_text = f"""
            <speak>
              <phoneme alphabet="ipa" ph="{ipa}">
                {clean_display_text}
              </phoneme>
            </speak>
            """
            synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)
        else:
            synthesis_input = texttospeech.SynthesisInput(text=text)

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
        filename = f"{item['id']:02d}_{clean_name}_{voice_name}_rate{rate}.{ext}"
        output_path = os.path.join(output_dir, filename)

        print(f"Generating: {filename}...")
        try:
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            with open(output_path, "wb") as out:
                out.write(response.audio_content)
        except Exception as e:
            print(f"  -> API Error: {e}")

if __name__ == "__main__":
    if os.path.exists(config["files"]["intermediate_data"]):
        os.remove(config["files"]["intermediate_data"])
    prepare_staging_file()
    generate_audio_from_staging()
