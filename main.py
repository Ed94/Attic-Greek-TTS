import os
import sys
import json
import tomli
from google.cloud import texttospeech

# --- 1. CONFIG LOADER ---
with open("config.toml", "rb") as f:
    config = tomli.load(f)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config["google"]["credentials_path"]

# --- 2. PREPARATION ---
def prepare_staging_file():
    input_path = config["files"]["input_text"]
    staging_path = config["files"]["intermediate_data"]

    # Pre-filled IPA for your specific words to ensure they work immediately
    # These use the "International Phonetic Alphabet"
    KNOWN_IPA = {
        "Κατηγορίαι": "ka.tɛː.go.ˈri.ai",
        "ὁμώνυμος": "ho.ˈmɔː.ny.mos",
        "ΣΤΟΙΧΕΙΩΝ αʹ": "stoi.ˈkʰeː.ɔːn ˈproː.tos"
    }

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found.")
        return

    work_list = []
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    for text in lines:
        ipa = KNOWN_IPA.get(text, "")
        work_list.append({
            "text": text,
            "ipa": ipa, 
            "status": "ready" if ipa else "needs_ipa"
        })

    with open(staging_path, "w", encoding="utf-8") as f:
        json.dump(work_list, f, indent=4, ensure_ascii=False)
    
    print(f"--- PREPARATION COMPLETE ---")
    print(f"Created '{staging_path}'. Please check it.")

# --- 3. GENERATION ---
def generate_audio_from_staging():
    staging_path = config["files"]["intermediate_data"]
    output_dir = config["tts"]["output_dir"]
    voice_name = config["tts"]["voice_name"]
    
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

        # If we have IPA, we wrap it in SSML <phoneme> tags.
        # This forces the English voice to pronounce the Greek sounds.
        if ipa:
            print(f"Generating: {text} -> [{ipa}]")
            ssml_text = f"""
            <speak>
              <phoneme alphabet="ipa" ph="{ipa}">
                {text}
              </phoneme>
            </speak>
            """
            synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)
        else:
            # Fallback if no IPA is provided (might sound weird with English voice)
            print(f"Generating (Raw Text): {text}")
            synthesis_input = texttospeech.SynthesisInput(text=text)

        # Voice Config
        # We extract the language code (e.g., "en-US") from the voice name
        lang_code = "-".join(voice_name.split("-")[:2])
        
        voice = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            name=voice_name
        )

        # Audio Config (LINEAR16 for WAV)
        encoding_map = {
            "LINEAR16": texttospeech.AudioEncoding.LINEAR16,
            "MP3": texttospeech.AudioEncoding.MP3
        }
        chosen_encoding = config["tts"].get("audio_encoding", "LINEAR16")
        
        audio_config = texttospeech.AudioConfig(
            audio_encoding=encoding_map.get(chosen_encoding, texttospeech.AudioEncoding.LINEAR16),
            speaking_rate=config["tts"]["speaking_rate"]
        )

        try:
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            
            ext = config["tts"]["output_extension"]
            safe_filename = text.replace(" ", "_").replace("ʹ", "").replace(".", "")[:20]
            output_path = os.path.join(output_dir, f"{safe_filename}.{ext}")
            
            with open(output_path, "wb") as out:
                out.write(response.audio_content)
                print(f"  -> Saved to {output_path}")
                
        except Exception as e:
            print(f"  -> API Error: {e}")

if __name__ == "__main__":
    if os.path.exists(config["files"]["intermediate_data"]):
        user_input = input("Staging file found. Generate Audio? [y/N]: ").lower()
        if user_input == 'y':
            generate_audio_from_staging()
        else:
            prepare_staging_file()
    else:
        prepare_staging_file()
