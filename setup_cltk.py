import os
import sys

try:
    from cltk.data.fetch import FetchCorpus
except ImportError:
    print("Error: CLTK not installed. Run 'uv sync' first.")
    sys.exit(1)

def download_data():
    print("--- DOWNLOADING GREEK LINGUISTIC DATA ---")
    print("This helps the AI know which vowels are long or short.")
    print("This might take 1-2 minutes...")
    
    fetcher = FetchCorpus(language="grc")
    
    # 1. Download the model data
    fetcher.import_corpus("grc_models_cltk")
    
    # 2. Check if it actually worked
    home = os.path.expanduser("~")
    expected_path = os.path.join(home, "cltk_data", "grc", "model", "grc_models_cltk")
    
    if os.path.exists(expected_path):
        print(f"\nSUCCESS! Data located at: {expected_path}")
    else:
        print(f"\nWARNING: Download finished, but folder not found at {expected_path}")
        print("CLTK might have installed it in a different user location, which is fine.")

if __name__ == "__main__":
    download_data()
