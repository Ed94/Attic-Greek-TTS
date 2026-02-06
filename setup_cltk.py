from cltk.data.fetch import FetchCorpus

print("Downloading Ancient Greek models... this may take a minute...")
corpus_downloader = FetchCorpus(language="grc")
# This downloads the necessary linguistic data
corpus_downloader.import_corpus("grc_models_cltk")
print("Done! You can now run the main script.")