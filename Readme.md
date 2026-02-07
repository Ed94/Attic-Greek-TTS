# Attic Greek TTS using Google's Chrip 3

idk if this is accurate. This was done in an afternoon. Used gemini 3 pro for the attic rules.

## Features

* **Polytonic Pitch Accent:** Simulates the "singing" melody of Ancient Greek (Acute/Circumflex).
* **Rhythm Control:** Enforces pauses based on punctuation and verse structure.
* **Breath System:** Automatically inserts breath pauses in long sentences at grammatically appropriate spots.

The input file can delimit sections with `---`. Each section is it's own wav. See the [input.txt](./input.txt) to get an idea.

## Prerequisites

* Python 3.9+
* A Google Cloud Account
* The Classical Language Toolkit (CLTK) needs the Greek models to calculate pronunciation.

I use uv package manager... (just have the sync the main directory since the script is a single file).

```py
uv sync
```

## Google Cloud Setup (Step-by-Step)

You need a Service Account Key (service-account.json) to allow this script to talk to Google’s servers.

### A. Create an Account & Project

Go to the Google Cloud Console.
Log in with your Gmail.
Note: You may need to enable billing to use the API. Google offers a generous free tier (usually 1 million characters per month for standard voices, fewer for Chirp), but they require a credit card for verification.
Click the project dropdown in the top bar and select “New Project”.
Name it AncientGreekTTS and click Create.

### B. Enable the Text-to-Speech API

In the search bar at the top, type “Text-to-Speech API”.
Select “Cloud Text-to-Speech API” from the marketplace results.
Click Enable.

### C. Create Credentials

In the left sidebar, go to APIs & Services > Credentials.
Click + CREATE CREDENTIALS (top of screen) and select Service Account.
Step 1: Give it a name (e.g., tts-runner). Click “Create and Continue”.
Step 2: (Optional) Select a role. Choose Basic > Owner (or Editor) to ensure it has permissions. Click “Continue”.
Step 3: Click “Done”.

### D. Download the Key (JSON)

You should now see your new Service Account in the list (e.g., tts-runner@ancientgreektts.iam.gserviceaccount.com).
Click on the Email address of that service account to edit it.
Go to the KEYS tab (top horizontal menu).
Click ADD KEY > Create new key.
Select JSON.
Click Create. A file will automatically download to your computer.
Rename this file to service-account.json and place it in the same folder as main.py.

## Configuration

You can tune the pacing, pitch, and voice settings within the [config.toml](./config.toml)
