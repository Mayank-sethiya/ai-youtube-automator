```markdown
# ⚡ AI YouTube & Instagram Video Creator — videoCreator.pyw

A focused desktop GUI application (single-file: `videoCreator.pyw`) that automates short-form video production: topic & script generation, voiceover (TTS), visual prompt & image generation, subtitle transcription/alignment, video assembly (MoviePy + FFmpeg), and optional YouTube upload.

Maintainer: @Mayank-sethiya

---

## Project structure
```
├── .gitignore
├── README.md
├── assets
│   ├── intro.mp3
│   ├── intro_car.png
│   ├── section_complete.mp3
│   ├── upload_complete.mp3
│   └── upload_notcomplete.mp3
└── videoCreator.pyw
```

---

## Key features
- LLM-driven topic & script generation (Gemini / HTTP LLMs)  
- Chunked TTS voiceover generation (ElevenLabs-compatible)  
- Visual-prompt authoring + image generation (Cloudflare / external APIs)  
- Subtitle transcription & alignment (faster-whisper)  
- Video composition, subtitle rendering, intro & watermark merging (moviepy + ffmpeg)  
- Optional YouTube upload with Google OAuth (desktop flow)  
- One-click autonomous mode + manual review UI

---

## Prerequisites
- Python 3.8+ (3.10+ recommended)  
- FFmpeg installed and on system PATH (required by moviepy)  
- Internet access for API calls (LLM, TTS, image endpoints)  
- Optional: GPU + CUDA for faster-whisper GPU acceleration

---

## Libraries used (imports in `videoCreator.pyw`)

Standard library
- os, json, threading, queue, random, time, base64, subprocess, sys, traceback, re, uuid, difflib, pickle

GUI & audio/video
- tkinter (tk, ttk, messagebox, scrolledtext, filedialog, simpledialog)  
- Pillow (PIL.Image, ImageTk)  
- pygame (optional — sound feedback)

Media processing & speech
- moviepy (ImageClip, AudioFileClip, concatenate_videoclips, TextClip, CompositeVideoClip, CompositeAudioClip, afx, VideoFileClip, concatenate_audioclips, ColorClip)  
- faster-whisper (WhisperModel)

Networking & APIs
- requests  
- google-api-python-client (optional — YouTube upload)  
- google-auth-oauthlib (optional — OAuth flow)  
- google-auth-httplib2 / google.auth.transport.requests (optional — token refresh)

Suggested pip install:
```bash
python -m pip install Pillow moviepy faster-whisper requests pygame google-api-python-client google-auth-oauthlib google-auth-httplib2
```

Suggested `requirements.txt`:
```
Pillow
moviepy
faster-whisper
requests
pygame
google-api-python-client
google-auth-oauthlib
google-auth-httplib2
```

---

## Run (quick)

1. Create & activate a virtual environment
- Windows
```powershell
python -m venv .venv
.venv\Scripts\activate
```
- macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies
```bash
pip install -r requirements.txt
# or the suggested pip install command above
```

3. Ensure FFmpeg is installed and on PATH:
- Windows: download FFmpeg and add the `bin` folder to PATH  
- macOS: `brew install ffmpeg`  
- Linux: `sudo apt install ffmpeg` (or distro equivalent)

4. Run the app
- Windows (recommended for `.pyw`):
```bash
python videoCreator.pyw
```
- macOS / Linux (optional): rename to `.py` then run:
```bash
mv videoCreator.pyw videoCreator.py
python3 videoCreator.py
```

On first run the app checks for FFmpeg and will display an error if not found.

---

## First-time configuration (in-app)
Open Settings → add the required keys/accounts:
- GEMINI_API_KEYS — LLM / text generation keys (one per line)  
- ELEVENLABS_API_KEYS — text-to-speech API keys (one per line)  
- CLOUDFLARE_ACCOUNTS — image generation accounts (account_id + api_token entries)  
- To enable YouTube uploads: place `client_secret.json` in the working directory (desktop OAuth credentials)

Settings are persisted to a local JSON config file created by the app.

---

## Runtime flow (what the app does)

1. Topic & Script
   - Enter a topic or click "💡 Suggest Topic" / "🧠 Suggest Similar".  
   - Script generation: calls the configured LLM to produce audio-ready script and subtitle text.

2. Voiceover (TTS)
   - Script is chunked and sent to ElevenLabs-compatible TTS endpoints.  
   - Audio chunks are merged into a single MP3 in `VOICEOVER_DIR`.

3. Transcription & Subtitles
   - `faster-whisper` transcribes the TTS audio to get word-level timestamps.  
   - Script words are aligned to transcription for precise subtitle timing.

4. Visual prompts & Images
   - The app creates scene-specific visual prompts and uses configured image accounts (Cloudflare etc.) to generate images.  
   - If One-Click is off, an image review window opens allowing replacements.

5. Intro & Main Video
   - Optional intro creation, then main video assembly: animated image clips, subtitles (TextClip), voiceover, background music — composed into a main video via moviepy.

6. Merge & Final
   - Intro + main video are merged; logo/watermark is applied if configured. Final MP4 is written to `OUTPUT_DIR`.

7. Metadata & YouTube Upload (optional)
   - The app can call the LLM to generate title/description/tags and then perform OAuth-based upload using Google API libraries.

Progress stages (visual stepper): Script → Voiceover → Images → Review → Intro → Main Video → Merging → YouTube Upload

Output and temp directories created at runtime:
- OUTPUT_DIR — final MP4 files  
- VOICEOVER_DIR — generated voiceover MP3s  
- TEMP_DIR — temporary chunks & files  
- generated_images — images created for scenes

---

## YouTube upload (notes)
- Create Desktop OAuth credentials in Google Cloud Console and enable YouTube Data API.  
- Place the downloaded `client_secret.json` next to `videoCreator.pyw`.  
- On first upload the app runs a local OAuth flow and stores tokens (pickle).  
- If upload fails, check credentials, scopes, enabled API, and the Progress Log for details.

---

## Troubleshooting (concise)
- FFmpeg not found → install and add to PATH.  
- Missing Google libraries → install `google-api-python-client google-auth-oauthlib google-auth-httplib2`.  
- Audio/TTS failures → verify `ELEVENLABS_API_KEYS` and network access.  
- Transcription errors → ensure `faster-whisper` is installed and model files are available; check CPU vs GPU.  
- Image generation errors → validate Cloudflare accounts or other image API credentials.  
- Check the in-app Progress Log for detailed messages and tracebacks.

---

## Security & best practices
- Never commit API keys or `client_secret.json` to the repo.  
- Use separate test keys while developing.  
- Add local secret files to `.gitignore` (examples provided).

---

## Files of interest
- `videoCreator.pyw` — main GUI application (all logic in this file)  
- `assets/` — UI audio & splash resources

---

## License
Add a LICENSE file (e.g., MIT) to define reuse and contribution terms.
```