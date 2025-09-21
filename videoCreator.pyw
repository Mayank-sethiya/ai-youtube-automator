import os
import json
import requests
import threading
import queue
import random
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from PIL import Image as PILImage, ImageTk
import PIL.Image
from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips, TextClip,
    CompositeVideoClip, CompositeAudioClip, afx, VideoFileClip,
    concatenate_audioclips, ColorClip
)
import base64
from faster_whisper import WhisperModel
import subprocess
import sys
import traceback
import re
import uuid
import difflib

# --- IMPORTS for YouTube API ---
try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import pickle
except ImportError:
    # This will be handled by a check in the VideoCreatorApp
    pass

# --- 1. Constants and Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'videos')
VOICEOVER_DIR = os.path.join(SCRIPT_DIR, 'voiceovers')
TEMP_DIR = os.path.join(SCRIPT_DIR, 'temp')

# --- YouTube API Constants ---
CLIENT_SECRET_FILE = os.path.join(SCRIPT_DIR, 'client_secret.json')
API_NAME = 'youtube'
API_VERSION = 'v3'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
TOKEN_PICKLE_FILE = os.path.join(SCRIPT_DIR, 'token.pickle')

class CancellationError(Exception):
    """Custom exception for handling cancellations."""
    pass

# --- Constants ---
GEMINI_TEXT_MODEL = 'gemini-1.5-flash-latest'
IMAGE_MODELS = {
    "Stable Diffusion XL (Standard)": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
    "SDXL Lightning (Fastest)": "@cf/bytedance/stable-diffusion-xl-lightning"
}
SDXL_DIMENSIONS = {
    "Widescreen (16:9)": {"width": 1024, "height": 576},
    "Portrait (9:16)": {"width": 576, "height": 1024},
    "Square (1:1)": {"width": 1024, "height": 1024},
}
SCRIPT_LANGUAGES = ["English", "Hindi", "Bhai Lang"]
VOICES = {
    "Adam (Male, American)": "pNInz6obpgDQGcFmaJgB",
    "Brian (Male, British)": "nPczCjzI2devNBz1zQrb"
}
MULTILINGUAL_VOICE_IDS = ["nPczCjzI2devNBz1zQrb", "pNInz6obpgDQGcFmaJgB"]
SUBTITLE_FONTS = ['Arial', 'Verdana', 'Roboto', 'Impact', 'Segoe UI Emoji']
SUBTITLE_POSITIONS = {"Bottom": "bottom", "Middle": "center", "Top": "top"}
SUBTITLE_COLORS = ["White", "Gold", "Yellow", "Cyan", "Lime", "Rainbow"]
RAINBOW_PALETTE = ['#FFD700', '#FF8C00', '#FF4500', '#ADFF2F', '#00FFFF', '#FF1493']
STAGES = ["Script", "Voiceover", "Images", "Review", "Intro", "Main Video", "Merging", "YouTube Upload"]

VOICE_PRESETS = {
    "pNInz6obpgDQGcFmaJgB": {"stability": 0.40, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True},
    "nPczCjzI2devNBz1zQrb": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True},
    "default": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.30, "use_speaker_boost": True}
}

# --- YouTube Uploader Functions ---
def youtube_authenticate(log_queue):
    log_queue.put(('log', "🔐 Authenticating with YouTube..."))
    if not os.path.exists(CLIENT_SECRET_FILE):
        log_queue.put(('log', f"❌ ERROR: client_secret.json not found. Please follow setup instructions."))
        return None
        
    creds = None
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log_queue.put(('log', "  -> Refreshing access token..."))
            try:
                creds.refresh(Request())
            except Exception as e:
                log_queue.put(('log', f"  -> ❌ Token refresh failed: {e}. Please delete token.pickle and re-run."))
                return None
        else:
            log_queue.put(('log', "  -> Performing one-time login... Please check your browser."))
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                log_queue.put(('log', f"  -> ❌ Login flow failed: {e}"))
                return None
            
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(creds, token)
            
    try:
        service = build(API_NAME, API_VERSION, credentials=creds)
        log_queue.put(('log', "✅ YouTube authentication successful."))
        return service
    except Exception as e:
        log_queue.put(('log', f"❌ YouTube authentication failed: {e}"))
        return None

def upload_to_youtube(service, video_path, title, description, tags, log_queue):
    log_queue.put(('log', f"\n🚀 Uploading '{title}' to YouTube..."))
    try:
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': [tag.strip() for tag in tags.split(',')],
                'categoryId': '28' 
            },
            'status': {
                'privacyStatus': 'private' 
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = service.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log_queue.put(('log', f"  -> Uploaded {int(status.progress() * 100)}%"))
        
        video_id = response.get('id')
        log_queue.put(('log', f"✅ Video uploaded successfully! Video ID: {video_id}"))
        log_queue.put(('log', f"  -> Watch it here (it is private): https://www.youtube.com/watch?v={video_id}"))
        return video_id
        
    except Exception as e:
        log_queue.put(('log', f"❌ YouTube upload failed: {e}"))
        return None

# --- Utility Functions ---
def save_config(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '', name)

# --- API and AI Content Generation Functions ---
def call_gemini_text_api(api_keys, system_prompt, user_prompt, log_queue):
    log_queue.put(('log', "⚙️ Calling Gemini Text API..."))
    if not api_keys:
        log_queue.put(('log', "❌ ERROR: No Google Gemini API keys."))
        return None, []
    remaining_keys, current_key_index = list(api_keys), 0
    while current_key_index < len(remaining_keys):
        api_key = remaining_keys[current_key_index]
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent?key={api_key}",
                headers={'Content-Type': 'application/json'},
                json={
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "systemInstruction": ({"parts": [{"text": system_prompt}]} if system_prompt else None)
                },
                timeout=90
            )
            if response.status_code in [400, 429] and ("quota" in response.text.lower() or "api key" in response.text.lower()):
                log_queue.put(('log', f"  -> Gemini Key #{current_key_index+1} issue. Trying next."))
                current_key_index += 1
                continue
            response.raise_for_status()
            return response.json()['candidates'][0]['content']['parts'][0]['text'].strip(), remaining_keys[current_key_index:]
        except requests.exceptions.RequestException as e:
            log_queue.put(('log', f"❌ ERROR with Gemini Key #{current_key_index+1}: {e}"))
            current_key_index += 1
        except (KeyError, IndexError):
            log_queue.put(('log', f"❌ ERROR: Failed to parse Gemini response."))
            current_key_index += 1
    log_queue.put(('log', "❌ All Gemini keys failed. 😢"))
    return None, []

def get_topic_system_prompt():
    return ("You are a cutting-edge YouTube Viral Trend Analyst. Your task is to simulate real-time trend analysis and generate a single, highly viral video title for a young Indian audience."
            "\n\n**Your Process:**"
            "\n1. **Analyze Current Formats:** Think about the newest, most successful YouTube Shorts formats like 'Surprising Historical Facts,' 'Myth Busting,' 'AI Experiments,' 'Cultural Secrets,' or 'Everyday Items with Hidden Features.'"
            "\n2. **Identify Curiosity Gaps:** The title must create a powerful information gap or challenge a common belief."
            "\n3. **Generate the Title:** Produce ONE unique and irresistible video title."
            "\n\n**Avoid Clichés:** Do not suggest generic topics about space, common animals, or overused historical events.")

def get_trending_topic(gemini_keys, log_queue, history=None):
    log_queue.put(('log', "\n💡 Researching unique viral topic..."))
    system_prompt = get_topic_system_prompt()
    history = history or []
    history_prompt = " For context, here are topics that have been suggested before. Avoid generating titles that are too similar to these:\n" + "\n".join(f"- {item}" for item in history[-20:]) if history else ""
    user_prompt = f"Generate a single, unique video title. Output ONLY the final title.{history_prompt}"
    topic, remaining_keys = call_gemini_text_api(gemini_keys, system_prompt, user_prompt, log_queue)
    if topic:
        log_queue.put(('log', f"✅ Topic suggestion received: {topic}"))
        return topic, remaining_keys
    return None, remaining_keys

def get_similar_topic(gemini_keys, base_topic, log_queue, history=None):
    log_queue.put(('log', f"\n🧠 Brainstorming topics similar to '{base_topic}'..."))
    system_prompt = "You are a creative brainstorm assistant. Your goal is to generate a new, viral video title that is thematically similar to a given topic but is a unique concept."
    history = history or []
    history_prompt = " Also, for context, here are topics that have been suggested before. Avoid generating titles that are too similar to these:\n" + "\n".join(f"- {item}" for item in history[-20:]) if history else ""
    user_prompt = f"Based on the core idea of '{base_topic}', generate one new, similar but distinct viral video title. Do not just rephrase the original. Come up with a new angle or a fresh take on the subject. Output ONLY the final title.{history_prompt}"
    topic, remaining_keys = call_gemini_text_api(gemini_keys, system_prompt, user_prompt, log_queue)
    if topic:
        log_queue.put(('log', f"✅ Similar topic received: {topic}"))
        return topic, remaining_keys
    return None, remaining_keys

def generate_script(gemini_keys, topic, language, log_queue, add_emojis=True):
    log_queue.put(('log', f"\n🎬 Generating script for topic: {topic}..."))
    
    emoji_instruction = "Use simple, single-color Unicode symbols for emojis (e.g., ★, ❤️, ✅, →) where they enhance the script's emotion." if add_emojis else ""
    
    influencer_prompt = (f"You are a professional YouTube influencer. Your tone is engaging and authoritative, yet easy to understand. CRITICAL: Write the script using simple, clear language with a natural conversational rhythm. Avoid complex sentence structures or words that are difficult for text-to-speech to pronounce. {emoji_instruction} Do NOT add closing remarks like 'peace out'. The script should end ONLY with the call to action. Only generate raw dialogue. Do NOT use headings or speaker names.")
    base_instruction = ("The script's length should be approximately 150 words, ideal for a 60-second video. IMPORTANT: End the script with a call to action encouraging viewers to like and subscribe. Output ONLY the script dialogue.")
    
    if language == "Hindi":
        user_prompt = (f"'{topic}' विषय के लिए एक वीडियो स्क्रिप्ट लिखें। भाषा शुद्ध हिंदी होनी चाहिए और देवनागरी लिपि में लिखी जानी चाहिए। बोलचाल की भाषा का प्रयोग करें जो समझने में आसान हो। {base_instruction}")
    elif language == "Bhai Lang":
        user_prompt = (f"'{topic}' विषय पर एक वीडियो स्क्रिप्ट लिखें। भाषा मुंबई की 'भाई भाषा' (टपोरी/बिंदास शैली) होनी चाहिए और देवनागरी लिपि में लिखी जानी चाहिए। इस टोन का प्रयोग करें: 'अरे भिड़ू, अपुन बोला ना, अपुन का सिस्टम अलग है! ये गाड़ी में जो म्यूजिक सिस्टम फिट कराया है ना, वो सुनके पूरी पब्लिक की वट्ट लग जाती है, क्या। बेस इतना तगड़ा है कि आजू-बाजू वालों की खिड़की का कांच भी 'धक-धक' करने लगता है। जब अपुन हाइवे पे इसको फुल वॉल्यूम पे बजाता है ना, तो लोग गाड़ी को नहीं, अपुन के स्पीकर को देखते रह जाते हैं। बोले तो... एक दम रापचिक माल है, फुल टोइंग-बाज़ी!' {base_instruction}")
    else: # English
        user_prompt = (f"Write a video script for '{topic}'. The language must be {language}. The tone should be conversational and engaging. {base_instruction}")

    audio_script, remaining_keys = call_gemini_text_api(gemini_keys, influencer_prompt, user_prompt, log_queue)
    if not audio_script:
        return None, None, remaining_keys

    log_queue.put(('log', "✅ Script generated successfully."))
    
    # MODIFICATION: Removed the Hinglish transliteration. Subtitles will use the original script.
    return audio_script, audio_script, remaining_keys

def generate_youtube_metadata(gemini_keys, script, log_queue):
    log_queue.put(('log', "\n✍️ Generating YouTube metadata..."))
    system_prompt = (
        "You are a YouTube SEO expert. Based on the provided video script, generate a catchy title, an engaging description, and a list of relevant tags. "
        "The title should be exciting and create a curiosity gap. "
        "The description should be a short paragraph summarizing the video's content and include a call to action. "
        "The tags should be a comma-separated list of 10-15 relevant keywords. "
        "Format the output as a JSON object with three keys: 'title', 'description', and 'tags'."
    )
    user_prompt = f"Here is the video script:\n\n\"{script}\"\n\nGenerate the YouTube metadata in JSON format."
    
    metadata_json, remaining_keys = call_gemini_text_api(gemini_keys, system_prompt, user_prompt, log_queue)
    
    if not metadata_json:
        return None, None, None, remaining_keys
        
    try:
        json_match = re.search(r'\{.*\}', metadata_json, re.DOTALL)
        if not json_match:
            raise json.JSONDecodeError("No JSON object found in response", metadata_json, 0)
        
        clean_json = json_match.group(0)
        data = json.loads(clean_json)
        
        title = data.get('title', 'AI Generated Video')
        description = data.get('description', 'Check out this amazing video generated by AI!')
        tags = data.get('tags', 'ai, technology, facts')
        
        log_queue.put(('log', "✅ YouTube metadata generated successfully."))
        return title, description, tags, remaining_keys
    except json.JSONDecodeError:
        log_queue.put(('log', "  -> ⚠️ WARNING: Failed to parse YouTube metadata JSON. Using fallback values."))
        return "AI Generated Video", script[:200], "ai, generated, video", remaining_keys

def split_text_into_chunks(text, max_length=2500):
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if not sentences or (len(sentences) == 1 and not sentences[0]): return []
    chunks, current_chunk = [], ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 > max_length and current_chunk:
            chunks.append(current_chunk.strip()); current_chunk = sentence.strip()
        else:
            current_chunk += (" " if current_chunk else "") + sentence.strip()
    if current_chunk: chunks.append(current_chunk.strip())
    return chunks

def generate_voiceover(script, api_keys, voice_id, topic, log_queue):
    log_queue.put(('log', "\n🎙️ Initiating voiceover generation with ElevenLabs..."))
    if not api_keys: log_queue.put(('log', "  -> ❌ ERROR: No ElevenLabs API keys provided.")); return None, []
    
    model_to_use = "eleven_multilingual_v2" if voice_id in MULTILINGUAL_VOICE_IDS else "eleven_turbo_v2"
    log_queue.put(('log', f"  -> ℹ️ Using model: {model_to_use}"))
    
    voice_settings = VOICE_PRESETS.get(voice_id, VOICE_PRESETS["default"]); log_queue.put(('log', f"  -> ⚙️ Applying voice preset for realism: {voice_settings}"))
    script_chunks = split_text_into_chunks(script, max_length=2500)
    if len(script_chunks) > 1: log_queue.put(('log', f"  -> ℹ️ Script is long, splitting into {len(script_chunks)} chunks."))
    audio_chunk_paths, session_keys = [], list(api_keys)
    for chunk_index, chunk in enumerate(script_chunks):
        if not chunk.strip(): continue
        generated = False
        while not generated and session_keys:
            api_key = session_keys[0]
            try:
                log_queue.put(('log', f"  -> 🔊 Generating audio for chunk {chunk_index + 1}/{len(script_chunks)}...")); response = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", headers={"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": api_key}, json={"text": chunk, "model_id": model_to_use, "voice_settings": voice_settings}, timeout=120)
                if response.status_code == 401: log_queue.put(('log', f"  -> 🟡 WARNING: Key ...{api_key[-4:]} is invalid. Removing for session.")); session_keys.pop(0); continue
                response.raise_for_status(); chunk_path = os.path.join(TEMP_DIR, f"voice_chunk_{uuid.uuid4().hex}.mp3")
                with open(chunk_path, 'wb') as f: f.write(response.content)
                audio_chunk_paths.append(chunk_path); generated = True
            except requests.exceptions.RequestException as e:
                error_text = str(e)
                if e.response is not None:
                    error_text = f"Status {e.response.status_code}: {e.response.text}"
                    if e.response.status_code == 404 and "voice_not_found" in e.response.text: log_queue.put(('log', f"  -> ❌ ERROR with Voice ID '{voice_id}'. It might be invalid or unavailable."))
                    else: log_queue.put(('log', f"  -> ❌ ERROR with Key ...{api_key[-4:]}: {error_text}"))
                log_queue.put(('log', "  -> ℹ️ Assuming key is faulty. Trying next...")); session_keys.pop(0)
        if not generated: log_queue.put(('log', f"  -> ❌ CRITICAL: Failed to generate audio for chunk {chunk_index + 1}.")); [os.remove(p) for p in audio_chunk_paths if os.path.exists(p)]; return None, session_keys
    if not audio_chunk_paths: log_queue.put(('log', "  -> ❌ ERROR: No audio chunks were created.")); return None, session_keys
    log_queue.put(('log', "  -> ⚙️ Merging audio chunks into a single file..."))
    try:
        output_path = os.path.join(VOICEOVER_DIR, f"{sanitize_filename(topic)[:30]}_{int(time.time())}.mp3")
        if len(audio_chunk_paths) == 1: os.rename(audio_chunk_paths[0], output_path)
        else:
            clips_to_merge = [AudioFileClip(p) for p in audio_chunk_paths]; final_audio = concatenate_audioclips(clips_to_merge); final_audio.write_audiofile(output_path, codec='mp3', logger=None)
            for clip in clips_to_merge: clip.close()
            for path in audio_chunk_paths: os.remove(path)
        log_queue.put(('log', "✅ Voiceover generation complete.")); return output_path, session_keys
    except Exception as e:
        log_queue.put(('log', f"  -> ❌ ERROR merging audio chunks: {e}\n{traceback.format_exc()}"))
        for path in audio_chunk_paths:
            if os.path.exists(path): os.remove(path)
        return None, session_keys

def generate_subtitles(audio_path, log_queue):
    log_queue.put(('log', "\n📝 Transcribing audio for subtitles..."))
    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8"); segments, _ = model.transcribe(audio_path, word_timestamps=True); all_words = [{'word': word.word, 'start': word.start, 'end': word.end} for segment in segments for word in segment.words]
        log_queue.put(('log', "✅ Audio transcribed successfully.")); return all_words
    except Exception as e: log_queue.put(('log', f"  -> ❌ ERROR generating subtitles: {e}")); return None

def align_script_with_subtitles(script_text, whisper_words, log_queue):
    log_queue.put(('log', "  -> ⚙️ Aligning script to transcription for perfect subtitles..."))
    try:
        script_words = re.findall(r"[\w\u0900-\u097F'-]+", script_text.lower())
        transcribed_words = [re.sub(r"[.,?!\"]", "", w['word'].lower()) for w in whisper_words]
        original_script_word_list = re.findall(r"[\w\u0900-\u097F'-]+|[.,?!\"]+", script_text)

        matcher = difflib.SequenceMatcher(None, script_words, transcribed_words, autojunk=False); corrected_words = []
        
        original_word_index = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for j in range(j1, j2):
                    if original_word_index < len(original_script_word_list) and j < len(whisper_words):
                        word_data = whisper_words[j].copy()
                        word_data['word'] = original_script_word_list[original_word_index]
                        corrected_words.append(word_data)
                        original_word_index += 1
            else:
                advance = max(i2 - i1, j2 - j1)
                original_word_index += advance

        if not corrected_words: corrected_words = whisper_words
        log_queue.put(('log', "✅ Subtitle alignment complete.")); return corrected_words
    except Exception as e: log_queue.put(('log', f"  -> ⚠️ WARNING: Subtitle alignment failed: {e}. Using original transcription.")); return whisper_words

def generate_visual_prompts(gemini_keys, script, log_queue, check_events_func, verbose=False):
    log_queue.put(('log', "\n🎨 Generating visual prompts with enhanced 'Film Director' strategy..."))
    remaining_keys = list(gemini_keys)

    # MODIFICATION: Upgraded system prompt for a more creative and consistent visual style.
    log_queue.put(('log', "  -> 🎬 Defining a consistent visual style and character..."))
    anchor_system_prompt = (
        "You are a concept artist. Based on the script, describe a central, visually interesting character OR subject and a consistent background style/setting. "
        "This will be the base 'anchor' for all images. Define a clear visual style (e.g., 'vintage polaroid', 'dramatic cinematic lighting', 'macro shot'). "
        "CRITICAL: Avoid words like 'cartoon', 'character', or '3D'. "
        "Example for a script about ancient coins: 'A wise old historian with glasses, examining a rare coin in a dimly lit, dusty library. The style is warm, focused lighting, with a shallow depth of field.'"
        "\n\n**Output ONLY the descriptive phrase.**"
    )
    anchor_user_prompt = f"Here is the script: \"{script}\"\n\nGenerate the anchor description."
    
    anchor_prompt, remaining_keys = call_gemini_text_api(remaining_keys, anchor_system_prompt, anchor_user_prompt, log_queue)

    if not anchor_prompt:
        log_queue.put(('log', "  -> ❌ CRITICAL ERROR: Failed to generate the anchor prompt. Aborting visuals."))
        return [], remaining_keys
        
    log_queue.put(('log', f"  -> ✅ Visual anchor established: '{anchor_prompt}'"))

    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    scene_count = max(5, min(10, len(sentences)))
    log_queue.put(('log', f"  -> 🎥 Breaking script into {scene_count} visual scenes..."))

    sentences_per_scene = (len(sentences) + scene_count - 1) // scene_count
    scene_scripts = [" ".join(sentences[i:i + sentences_per_scene]) for i in range(0, len(sentences), sentences_per_scene)]
    
    visual_prompts = []
    
    for i, scene_text in enumerate(scene_scripts):
        check_events_func()
        log_queue.put(('log', f"  -> ⚙️ Generating dynamic shot for scene {i+1}/{len(scene_scripts)}..."))
        
        # MODIFICATION: Upgraded system prompt to ask for varied camera angles and actions.
        action_system_prompt = (
            "You are a film director describing a specific shot. Based on the script line, describe the scene, including the character's action/expression AND a camera angle. Be creative with the angles. "
            "**CRITICAL: VARY THE SHOTS! Use different camera angles like:**\n"
            "- Extreme close-up shot of...\n"
            "- Low-angle shot looking up at...\n"
            "- Dutch angle shot showing...\n"
            "- Wide shot of the character in the setting...\n"
            "- Over-the-shoulder shot...\n"
            "- Point-of-view (POV) shot...\n"
            "Example Output: 'A dramatic low-angle shot of the historian gasping in surprise, holding the coin up to the light.'"
            "\n\n**Output ONLY the short scene description.**"
        )
        action_user_prompt = f"Here is the script line: \"{scene_text}\"\n\nDescribe the shot."
        
        scene_description, remaining_keys = call_gemini_text_api(remaining_keys, action_system_prompt, action_user_prompt, log_queue)
        
        if scene_description:
            positive_prompt = f"{anchor_prompt}, {scene_description}. cinematic, 8K, sharp focus, hyper-detailed, professional color grading, shot on Fujifilm."
            negative_prompt = "cartoon, anime, drawing, 3d render, painting, watermark, text, signature, ugly, deformed, bad anatomy, extra limbs, poorly drawn hands, morbid, mutilated, abstract, childish, simple"
            
            if verbose:
                log_queue.put(('log', f"\n    ✨ SCENE {i+1} PROMPT ✨"))
                log_queue.put(('log', f"    POSITIVE: {positive_prompt}"))
                log_queue.put(('log', f"    NEGATIVE: {negative_prompt}"))

            visual_prompts.append({
                "positive": positive_prompt,
                "negative": negative_prompt
            })
        else:
            log_queue.put(('log', f"  -> ❌ ERROR: Failed to generate action for scene {i+1}. Skipping."))
            continue

    if not visual_prompts:
        log_queue.put(('log', "  -> ❌ CRITICAL ERROR: No visual prompts were generated."))
        return [], remaining_keys

    log_queue.put(('log', "✅ All scene prompts generated successfully."))
    return visual_prompts, remaining_keys


def generate_images_cloudflare(prompts_data, accounts, log_queue, image_model_id, check_events_func):
    log_queue.put(('log', "\n📸 Generating images with Cloudflare AI..."))
    if not accounts: log_queue.put(('log', "  -> ❌ ERROR: No Cloudflare accounts provided.")); return []
    
    MIN_FILE_SIZE_BYTES = 1048576  # 1 MB

    image_paths, session_accounts = [], list(accounts); generated_images_dir = os.path.join(SCRIPT_DIR, "generated_images"); os.makedirs(generated_images_dir, exist_ok=True)
    for i, prompt_info in enumerate(prompts_data):
        check_events_func()
        if not session_accounts: log_queue.put(('log', "  -> ❌ All Cloudflare accounts have hit their limit. Aborting.")); break
        generated = False
        while not generated and session_accounts:
            account = session_accounts[0]; account_id, api_token = account['account_id'], account['api_token']; log_queue.put(('log', f"  -> ⚙️ Generating image {i+1}/{len(prompts_data)} with Account ...{account_id[-6:]}"))
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{image_model_id}"; headers = {"Authorization": f"Bearer {api_token}"}; payload = {"prompt": prompt_info['positive'], "negative_prompt": prompt_info['negative']}
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=180)
                if response.status_code == 429: log_queue.put(('log', f"  -> 🟡 Account ...{account_id[-6:]} rate limited. Trying next.")); session_accounts.pop(0); continue
                response.raise_for_status(); file_path = os.path.join(generated_images_dir, f"image_{i+1}_{uuid.uuid4().hex[:6]}.png")
                with open(file_path, "wb") as f: f.write(response.content)
                image_size = os.path.getsize(file_path) 
                if image_size < MIN_FILE_SIZE_BYTES: 
                    os.remove(file_path) 
                    raise ValueError(f"Image quality below threshold ({image_size/1048576:.2f}MB). Likely out of neurons.")
                image_paths.append(file_path); log_queue.put(('log', f"  -> ✅ Image {i+1} saved.")); generated = True
            except (requests.exceptions.RequestException, ValueError) as e: 
                error_details = str(e) 
                if isinstance(e, requests.exceptions.RequestException) and e.response: 
                    error_details = e.response.text
                log_queue.put(('log', f"  -> ❌ ERROR with Account ...{account_id[-6:]}: {error_details}")); log_queue.put(('log', "  -> ℹ️ Removing faulty account for this session.")); session_accounts.pop(0); continue
    log_queue.put(('log', f"✅ Image generation complete. {len(image_paths)} images created.")); return image_paths

def create_animated_clip(image_path, duration, target_size, effect):
    try:
        img_clip = ImageClip(image_path).set_duration(duration); aspect_ratio = target_size[0] / target_size[1]; img_aspect_ratio = img_clip.w / img_clip.h
        if img_aspect_ratio > aspect_ratio: img_clip = img_clip.resize(height=target_size[1])
        else: img_clip = img_clip.resize(width=target_size[0])
        img_clip = img_clip.crop(x_center=img_clip.w/2, y_center=img_clip.h/2, width=target_size[0], height=target_size[1])
        if effect == 'slow_zoom_in': animated_clip = img_clip.resize(lambda t: 1 + 0.1 * (t / duration))
        elif effect == 'slow_zoom_out': animated_clip = img_clip.resize(lambda t: 1.1 - 0.1 * (t / duration))
        elif effect == 'fast_zoom_in': animated_clip = img_clip.resize(lambda t: 1 + 0.3 * (t / duration))
        elif effect in ['pan_left', 'pan_right']:
            pan_factor = 1.2; resized_clip = img_clip.resize(width=target_size[0] * pan_factor); move_x = (resized_clip.w - target_size[0]) / 2; start_x = -move_x if effect == 'pan_left' else move_x; animated_clip = resized_clip.set_position(lambda t: (start_x + (-start_x) * (t / duration), 'center'))
        elif effect in ['pan_up', 'pan_down']:
            pan_factor = 1.2; resized_clip = img_clip.resize(height=target_size[1] * pan_factor); move_y = (resized_clip.h - target_size[1]) / 2; start_y = -move_y if effect == 'pan_up' else move_y; animated_clip = resized_clip.set_position(lambda t: ('center', start_y + (-start_y) * (t / duration)))
        elif effect == 'subtle_rotate': animated_clip = img_clip.rotate(lambda t: 0.5 * (t / duration), expand=True)
        else: animated_clip = img_clip.resize(lambda t: 1 + 0.1 * (t / duration))
        return animated_clip.crop(x_center=animated_clip.w/2, y_center=animated_clip.h/2, width=target_size[0], height=target_size[1])
    except Exception as e:
        print(f"Error creating animated clip for {image_path}: {e}"); return ColorClip(target_size, color=(0,0,0), duration=duration)

def create_intro_video(options, log_queue, check_events_func):
    log_queue.put(('log', "\n✨ Assembling dynamic video intro...")); check_events_func()
    if len(options['image_paths']) < 1: log_queue.put(('log', "  -> ❌ Not enough images for intro. Skipping.")); return None
    try:
        sample_size = min(len(options['image_paths']), 15); intro_clips = [create_animated_clip(p, duration=0.4, target_size=options['target_size'], effect='slow_zoom_in').set_duration(0.2).crossfadein(0.1) for p in random.sample(options['image_paths'], sample_size)]
        intro_video = concatenate_videoclips(intro_clips, method="compose").fadeout(0.2); sub_options = options['subtitle_options']; title_color = 'gold' if sub_options['color'] == "Rainbow" else sub_options['color'].lower()
        title_clip = TextClip(options['topic'].upper(), fontsize=int(options['target_size'][1]/12), color=title_color, font=sub_options['font'], stroke_color='black', stroke_width=sub_options['border']+1, method='caption', size=(options['target_size'][0]*0.9, None), align='center').set_duration(intro_video.duration).set_position('center')
        final_intro = CompositeVideoClip([intro_video, title_clip])
        if options.get('bg_music_path') and os.path.exists(options['bg_music_path']):
            bg_music = (AudioFileClip(options['bg_music_path']).fx(afx.volumex, options['bg_music_vol']).fx(afx.audio_loop, duration=final_intro.duration)); final_intro = final_intro.set_audio(bg_music)
        intro_path = os.path.join(TEMP_DIR, "intro.mp4")
        final_intro.write_videofile(intro_path, codec='libx264', logger=None, threads=4, preset='medium')
        log_queue.put(('log', "✅ Intro video created successfully.")); return intro_path
    except Exception as e:
        if not isinstance(e, CancellationError): log_queue.put(('log', f"  -> ❌ Error creating intro: {e}"))
        return None

def group_words_into_lines(words, max_words_per_line, max_width, font_info):
    lines, current_line = [], [];
    if not words: return []
    for word_data in words:
        potential_line = current_line + [word_data]; line_text = " ".join([word['word'] for word in potential_line]); probe_clip = TextClip(line_text.upper(), fontsize=font_info['size'], font=font_info['font'], stroke_color='black', stroke_width=font_info['border'])
        if probe_clip.size[0] > max_width and current_line: lines.append(current_line); current_line = [word_data]
        else: current_line = potential_line
        if len(current_line) >= max_words_per_line or word_data['word'].endswith(('.', '?', '!')):
            if current_line: lines.append(current_line)
            current_line = []
    if current_line: lines.append(current_line)
    return lines

def create_main_video(options, log_queue, check_events_func):
    log_queue.put(('log', "\n🎞️ Assembling main video content..."))
    if not options['image_paths']: log_queue.put(('log', "  -> ❌ No images found for main video.")); return None
    try:
        effects = ['slow_zoom_in', 'pan_right', 'slow_zoom_out', 'pan_up', 'fast_zoom_in', 'pan_left', 'pan_down', 'subtle_rotate']
        main_clips = [create_animated_clip(p, options['duration'] / len(options['image_paths']), options['target_size'], effect=effects[i % len(effects)]) for i, p in enumerate(options['image_paths'])]
        visual_base = concatenate_videoclips(main_clips).set_fps(24); final_visual_layers = [visual_base]
        if options.get('subtitle_data'):
            sub_options = options['subtitle_options']; base_fontsize = int(options['target_size'][1] * (sub_options['size'] / 100)); is_rainbow = sub_options['color'] == "Rainbow"; default_highlight_color = 'gold' if sub_options['color'] in ["White", "Rainbow"] else sub_options['color']
            font_info = {'size': base_fontsize, 'font': sub_options['font'], 'border': sub_options['border']}; safe_width = options['target_size'][0] * 0.9; word_groups = group_words_into_lines(options['subtitle_data'], 4, safe_width, font_info); global_word_index = 0
            for line_words in word_groups:
                if not line_words: continue
                line_text = " ".join([word['word'] for word in line_words]); line_start_time = line_words[0]['start']; line_end_time = line_words[-1]['end']; size_probe = TextClip(line_text.upper(), fontsize=base_fontsize, font=sub_options['font'], stroke_color='black', stroke_width=sub_options['border']); line_width, line_height = size_probe.size; target_w, target_h = options['target_size']
                pos_map = {"top": ((target_w - line_width) / 2, target_h * 0.1), "center": ((target_w - line_width) / 2, (target_h - line_height) / 2), "bottom": ((target_w - line_width) / 2, target_h * 0.9 - line_height)}; start_x, start_y = pos_map.get(sub_options['position'], pos_map['bottom'])
                
                base_clip = TextClip(line_text.upper(), fontsize=base_fontsize, color='#CCCCCC', font=sub_options['font'], stroke_color='black', stroke_width=sub_options['border']).set_start(line_start_time).set_duration(line_end_time - line_start_time).set_position((start_x, start_y)); final_visual_layers.append(base_clip); cumulative_width = 0
                for i, word_data in enumerate(line_words):
                    highlight_color = RAINBOW_PALETTE[global_word_index % len(RAINBOW_PALETTE)] if is_rainbow else default_highlight_color; highlight_word_clip = TextClip(word_data['word'].upper(), fontsize=base_fontsize, color=highlight_color.lower(), font=sub_options['font'], stroke_color='black', stroke_width=sub_options['border'])
                    highlight_clip = highlight_word_clip.set_start(word_data['start']).set_duration(word_data['end'] - word_data['start']).set_position((start_x + cumulative_width, start_y)); final_visual_layers.append(highlight_clip)
                    space_clip = TextClip(" ", fontsize=base_fontsize, font=sub_options['font']); cumulative_width += highlight_word_clip.size[0] + space_clip.size[0]; global_word_index += 1
        final_video_clip = CompositeVideoClip(final_visual_layers, size=options['target_size']); final_audio_clips = []
        
        if options.get('voiceover_path'):
            voiceover_clip = AudioFileClip(options['voiceover_path'])
            normalized_voiceover = voiceover_clip.fx(afx.audio_normalize)
            final_audio_clips.append(normalized_voiceover)
            
        if options.get('bg_music_path'):
            final_audio_clips.append(AudioFileClip(options['bg_music_path']).fx(afx.volumex, options['bg_music_vol']).fx(afx.audio_loop, duration=final_video_clip.duration))
        
        if final_audio_clips:
            final_video_clip = final_video_clip.set_audio(CompositeAudioClip(final_audio_clips))
            
        main_video_path = os.path.join(TEMP_DIR, "main_video.mp4")
        final_video_clip.write_videofile(main_video_path, codec='libx264', audio_codec='aac', logger=None, threads=4, preset='medium')
        log_queue.put(('log', "✅ Main video created successfully.")); return main_video_path
    except Exception as e:
        if not isinstance(e, CancellationError): log_queue.put(('log', f"  -> ❌ Error assembling main video: {e}")); log_queue.put(('log', f"  -> ℹ️ Full traceback: {traceback.format_exc()}"))
        return None

def merge_videos(intro_path, main_path, output_path, options, log_queue, check_events_func):
    log_queue.put(('log', "\n🤝 Merging all video components...")); check_events_func()
    try:
        clips_to_merge = [VideoFileClip(p) for p in [intro_path, main_path] if p and os.path.exists(p)]
        if not clips_to_merge: log_queue.put(('log', "  -> ❌ Cannot merge: No video clips found.")); return None
        final_clip = concatenate_videoclips(clips_to_merge); final_layers = [final_clip]
        if options.get('logo_path') and os.path.exists(options['logo_path']):
            watermark_w = int(options['target_size'][0] * 0.15); logo_clip = ImageClip(options['logo_path']); watermark_h = int(watermark_w * (logo_clip.h / logo_clip.w))
            watermark = (logo_clip.set_duration(final_clip.duration).resize(width=watermark_w).set_opacity(options.get('logo_opacity', 0.5)).set_position((final_clip.w - watermark_w - 10, final_clip.h - watermark_h - 10))); final_layers.append(watermark)
        CompositeVideoClip(final_layers).write_videofile(output_path, codec='libx264', audio_codec='aac', logger=None, threads=4, preset='medium')
        for c in clips_to_merge: c.close()
        for p in [intro_path, main_path]:
            if p and os.path.exists(p): os.remove(p)
        log_queue.put(('log', f"\n🎉 Final video saved to:\n{os.path.abspath(output_path)}")); return output_path
    except Exception as e:
        if not isinstance(e, CancellationError): log_queue.put(('log', f"  -> ❌ Error during final merge: {e}"))
        return None

# --- GUI Classes (No changes in this part, but included for completeness) ---

class ProgressStepper(ttk.Frame):
    def __init__(self, parent, stages, colors):
        super().__init__(parent); self.stages, self.colors = stages, colors; self.canvas = tk.Canvas(self, bg=colors['bg'], highlightthickness=0, height=90); self.canvas.pack(fill='x', expand=True); self.items = {}; self.current_stage_index = -1; self.canvas.bind("<Configure>", self._draw)
    def _draw(self, event=None):
        self.canvas.delete("all"); width = self.canvas.winfo_width(); self.items = {'nodes': [], 'lines': []};
        if width <= 1: return
        num_stages, padding, y_center, radius, y_text = len(self.stages), 40, 50, 15, 80
        for i in range(num_stages - 1):
            x1 = padding + (width - 2 * padding) / (num_stages - 1) * i; x2 = padding + (width - 2 * padding) / (num_stages - 1) * (i + 1); self.items['lines'].append(self.canvas.create_line(x1, y_center, x2, y_center, fill=self.colors['pending'], width=3))
        for i, stage in enumerate(self.stages):
            x = padding + (width - 2 * padding) * i / (num_stages - 1) if num_stages > 1 else width / 2; oval = self.canvas.create_oval(x - radius, y_center - radius, x + radius, y_center + radius, fill=self.colors['card'], outline=self.colors['pending'], width=2); label = self.canvas.create_text(x, y_center, text=str(i + 1), fill=self.colors['pending_text'], font=('Segoe UI', 10, 'bold')); text = self.canvas.create_text(x, y_text, text=stage, fill=self.colors['pending_text'], font=('Segoe UI', 9)); self.items['nodes'].append({'oval': oval, 'label': label, 'text': text})
        self.update_stage(self.current_stage_index, 'pending')
    def update_stage(self, stage_index, status):
        self.current_stage_index = stage_index
        if stage_index < 0 or stage_index >= len(self.items['nodes']): return
        color_map = {"pending": (self.colors['pending'], self.colors['pending_text']), "in_progress": (self.colors['accent'], 'white'), "complete": (self.colors['success'], 'white'), "error": (self.colors['error'], 'white')}
        for i in range(len(self.stages)):
            node = self.items['nodes'][i]
            if i < stage_index:
                self.canvas.itemconfig(node['oval'], outline=self.colors['success'], fill=self.colors['success']); self.canvas.itemconfig(node['label'], fill='white', text="✓"); self.canvas.itemconfig(node['text'], fill=self.colors['text'])
                if i < len(self.items['lines']): self.canvas.itemconfig(self.items['lines'][i], fill=self.colors['success'])
            elif i == stage_index:
                color, text_color = color_map.get(status, color_map['pending']); text = {"complete": "✓", "error": "✗"}.get(status, str(i + 1)); self.canvas.itemconfig(node['oval'], outline=color, fill=color if status != 'pending' else self.colors['card']); self.canvas.itemconfig(node['label'], fill=text_color, text=text); self.canvas.itemconfig(node['text'], fill=self.colors['text'] if status != 'pending' else self.colors['pending_text'])
            else:
                self.canvas.itemconfig(node['oval'], outline=self.colors['pending'], fill=self.colors['card']); self.canvas.itemconfig(node['label'], fill=self.colors['pending_text'], text=str(i+1)); self.canvas.itemconfig(node['text'], fill=self.colors['pending_text'])
                if i > 0 and i-1 < len(self.items['lines']): self.canvas.itemconfig(self.items['lines'][i-1], fill=self.colors['pending'])
    def reset(self): self.current_stage_index = -1; self._draw()

class CloudflareDialog(simpledialog.Dialog):
    def body(self, master):
        self.title("Add Cloudflare Account")
        ttk.Label(master, text="Account ID:").grid(row=0, sticky='w', padx=5, pady=5)
        ttk.Label(master, text="API Token:").grid(row=1, sticky='w', padx=5, pady=5)
        self.id_entry = ttk.Entry(master, width=50)
        self.token_entry = ttk.Entry(master, width=50)
        self.id_entry.grid(row=0, column=1, padx=5)
        self.token_entry.grid(row=1, column=1, padx=5)
        return self.id_entry
    def validate(self):
        if not self.id_entry.get().strip() or not self.token_entry.get().strip():
            messagebox.showwarning("Input Error", "Both Account ID and API Token are required.", parent=self)
            return 0
        return 1
    def apply(self):
        self.result = (self.id_entry.get().strip(), self.token_entry.get().strip())

class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config; self.transient(parent); self.grab_set(); self.title("API Settings"); self.geometry("700x750")
        self.is_fullscreen = False; self.bind("<F11>", self.toggle_fullscreen); self.bind("<Escape>", self.exit_fullscreen)
        main_frame = ttk.Frame(self, padding=10); main_frame.pack(fill='both', expand=True); main_frame.columnconfigure(0, weight=1)

        gemini_frame = ttk.LabelFrame(main_frame, text="Google Gemini API Keys", padding=10); gemini_frame.grid(row=0, column=0, sticky='ew'); gemini_frame.columnconfigure(0, weight=1)
        self.gemini_text = scrolledtext.ScrolledText(gemini_frame, height=5, wrap=tk.WORD); self.gemini_text.grid(row=0, column=0, sticky='ew'); self.gemini_text.insert(tk.END, "\n".join(config.get('GEMINI_API_KEYS', [])))
        gem_btn_frame = ttk.Frame(gemini_frame); gem_btn_frame.grid(row=1, column=0, pady=(5,0), sticky='w')
        ttk.Button(gem_btn_frame, text="➕ Add Key", command=self.add_gemini_key).pack(side='left', padx=(0,5))
        self.remove_gemini_button = ttk.Button(gem_btn_frame, text="➖ Remove Selected", command=self.remove_gemini_key, state='disabled'); self.remove_gemini_button.pack(side='left')
        self.gemini_text.bind("<KeyRelease>", lambda e: self.on_text_select(self.gemini_text, self.remove_gemini_button)); self.gemini_text.bind("<ButtonRelease>", lambda e: self.on_text_select(self.gemini_text, self.remove_gemini_button))

        eleven_frame = ttk.LabelFrame(main_frame, text="ElevenLabs API Keys", padding=10); eleven_frame.grid(row=1, column=0, sticky='ew', pady=10); eleven_frame.columnconfigure(0, weight=1)
        self.elevenlabs_text = scrolledtext.ScrolledText(eleven_frame, height=5, wrap=tk.WORD); self.elevenlabs_text.grid(row=0, column=0, sticky='ew'); self.elevenlabs_text.insert(tk.END, "\n".join(config.get('ELEVENLABS_API_KEYS', [])))
        eleven_btn_frame = ttk.Frame(eleven_frame); eleven_btn_frame.grid(row=1, column=0, pady=(5,0), sticky='w')
        ttk.Button(eleven_btn_frame, text="➕ Add Key", command=self.add_elevenlabs_key).pack(side='left', padx=(0,5))
        self.remove_elevenlabs_button = ttk.Button(eleven_btn_frame, text="➖ Remove Selected", command=self.remove_elevenlabs_key, state='disabled'); self.remove_elevenlabs_button.pack(side='left')
        self.elevenlabs_text.bind("<KeyRelease>", lambda e: self.on_text_select(self.elevenlabs_text, self.remove_elevenlabs_button)); self.elevenlabs_text.bind("<ButtonRelease>", lambda e: self.on_text_select(self.elevenlabs_text, self.remove_elevenlabs_button))

        cf_frame = ttk.LabelFrame(main_frame, text="Cloudflare Accounts", padding=10); cf_frame.grid(row=2, column=0, sticky='nsew'); main_frame.rowconfigure(2, weight=1); cf_frame.columnconfigure(0, weight=1); cf_frame.rowconfigure(0, weight=1)
        self.cloudflare_tree = ttk.Treeview(cf_frame, columns=("account_id", "api_token"), show="headings"); self.cloudflare_tree.heading("account_id", text="Account ID"); self.cloudflare_tree.heading("api_token", text="API Token"); self.cloudflare_tree.column("account_id", width=250); self.cloudflare_tree.column("api_token", width=250); self.cloudflare_tree.grid(row=0, column=0, sticky='nsew')
        for account in config.get('CLOUDFLARE_ACCOUNTS', []):
            if isinstance(account, dict) and 'account_id' in account and 'api_token' in account: self.cloudflare_tree.insert("", "end", values=(account['account_id'], account['api_token']))
        cf_btn_frame = ttk.Frame(cf_frame); cf_btn_frame.grid(row=1, column=0, pady=(10,0), sticky='w')
        ttk.Button(cf_btn_frame, text="➕ Add Account", command=self.add_cloudflare_account).pack(side='left', padx=(0,5))
        self.remove_cf_button = ttk.Button(cf_btn_frame, text="➖ Remove Selected", command=self.remove_cloudflare_account, state='disabled'); self.remove_cf_button.pack(side='left')
        self.cloudflare_tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        bottom_btn_frame = ttk.Frame(main_frame); bottom_btn_frame.grid(row=3, column=0, pady=15)
        ttk.Button(bottom_btn_frame, text="Save and Close", command=self.save_and_close).pack(side='left', padx=5)
        ttk.Button(bottom_btn_frame, text="Fullscreen (F11)", command=self.toggle_fullscreen).pack(side='left', padx=5)

    def add_gemini_key(self): self._add_key_to_text("Gemini API Key", self.gemini_text)
    def remove_gemini_key(self): self._remove_selected_text_line(self.gemini_text)
    def add_elevenlabs_key(self): self._add_key_to_text("ElevenLabs API Key", self.elevenlabs_text)
    def remove_elevenlabs_key(self): self._remove_selected_text_line(self.elevenlabs_text)
    def _add_key_to_text(self, title, text_widget):
        key = simpledialog.askstring("Input", f"Enter {title}:", parent=self)
        if key and key.strip():
            current_text = text_widget.get(1.0, tk.END).strip()
            text_widget.insert(tk.END, ('\n' if current_text else '') + key.strip())
    def _remove_selected_text_line(self, text_widget):
        if not text_widget.tag_ranges(tk.SEL): return
        if not messagebox.askyesno("Confirm", "Are you sure you want to remove the selected key(s)?", parent=self): return
        try:
            start, end = text_widget.index(tk.SEL_FIRST), text_widget.index(tk.SEL_LAST)
            line_start = text_widget.index(f"{start} linestart")
            line_end_with_newline = text_widget.index(f"{end} lineend + 1 chars")
            text_widget.delete(line_start, line_end_with_newline)
        except tk.TclError: pass
    def on_text_select(self, text_widget, remove_button): remove_button.config(state="normal" if text_widget.tag_ranges(tk.SEL) else "disabled")
    def add_cloudflare_account(self):
        dialog = CloudflareDialog(self)
        if dialog.result: self.cloudflare_tree.insert("", "end", values=dialog.result)
    def remove_cloudflare_account(self):
        if not self.cloudflare_tree.selection(): return
        if messagebox.askyesno("Confirm", "Are you sure you want to remove the selected account(s)?", parent=self):
            for item in self.cloudflare_tree.selection(): self.cloudflare_tree.delete(item)
    def on_tree_select(self, event=None): self.remove_cf_button.config(state="normal" if self.cloudflare_tree.selection() else "disabled")
    def toggle_fullscreen(self, event=None): self.is_fullscreen = not self.is_fullscreen; self.attributes("-fullscreen", self.is_fullscreen)
    def exit_fullscreen(self, event=None):
        if self.is_fullscreen: self.is_fullscreen = False; self.attributes("-fullscreen", False)
    def save_and_close(self):
        self.config['GEMINI_API_KEYS'] = [k.strip() for k in self.gemini_text.get(1.0, tk.END).strip().split('\n') if k.strip()]
        self.config['ELEVENLABS_API_KEYS'] = [k.strip() for k in self.elevenlabs_text.get(1.0, tk.END).strip().split('\n') if k.strip()]
        cf_accounts = []
        for item_id in self.cloudflare_tree.get_children():
            values = self.cloudflare_tree.item(item_id, 'values')
            if len(values) == 2: cf_accounts.append({"account_id": values[0], "api_token": values[1]})
        self.config['CLOUDFLARE_ACCOUNTS'] = cf_accounts
        save_config(self.config); self.destroy()

class YouTubeUploadDialog(simpledialog.Dialog):
    def __init__(self, parent, title, initial_data):
        self.initial_data = initial_data
        super().__init__(parent, title)

    def body(self, master):
        self.title("Upload to YouTube")
        
        ttk.Label(master, text="Title:").grid(row=0, sticky='w', padx=5, pady=2)
        self.title_entry = ttk.Entry(master, width=60)
        self.title_entry.grid(row=1, padx=5, pady=2, columnspan=2)

        ttk.Label(master, text="Description:").grid(row=2, sticky='w', padx=5, pady=2)
        self.desc_text = scrolledtext.ScrolledText(master, width=60, height=5, wrap=tk.WORD)
        self.desc_text.grid(row=3, padx=5, pady=2, columnspan=2)
        
        ttk.Label(master, text="Tags (comma-separated):").grid(row=4, sticky='w', padx=5, pady=2)
        self.tags_entry = ttk.Entry(master, width=60)
        self.tags_entry.grid(row=5, padx=5, pady=2, columnspan=2)
        
        self.title_entry.insert(0, self.initial_data.get('title', ''))
        self.desc_text.insert(tk.END, self.initial_data.get('description', ''))
        self.tags_entry.insert(0, self.initial_data.get('tags', ''))
        
        return self.title_entry

    def apply(self):
        self.result = {
            "title": self.title_entry.get(),
            "description": self.desc_text.get("1.0", tk.END).strip(),
            "tags": self.tags_entry.get()
        }

class VideoCreatorApp:
    def __init__(self, root):
        self.root = root; self.root.title("AI Video Creator"); self.root.geometry("1100x900"); self.config = load_config()
        self.audio_script_cache = None 
        self.subtitle_script_cache = None
        self.script_box_mode = 'subtitle'
        self.last_video_path = None
        self.youtube_service = None
        
        try:
            from googleapiclient.discovery import build
        except ImportError:
            messagebox.showerror("Missing Libraries", "YouTube upload libraries are missing. Please run: \npip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
            root.destroy()
            return
        
        self.pause_event, self.cancel_event = threading.Event(), threading.Event()
        self.image_review_event = threading.Event()
        self.final_image_paths = []
        
        self._setup_styles()
        self._create_widgets()
        self._load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def on_closing(self): self._save_settings(); self.root.destroy()
    def _setup_styles(self):
        self.colors = {'bg': "#F0F0F0", 'card': "#FFFFFF", 'text': "#000000", 'accent': "#007ACC", 'success': "#28A745", 'error': "#DC3545", 'pending': "#DDDDDD", 'pending_text': "#888888"}
        self.style = ttk.Style(); self.style.theme_use('clam'); self.style.configure('.', background=self.colors['bg'], foreground=self.colors['text'], fieldbackground=self.colors['card'], bordercolor=self.colors['pending']); self.style.configure('TLabel', background=self.colors['bg'], foreground=self.colors['text']); self.style.configure('TButton', font=('Segoe UI', 10, 'bold'), padding=5); self.style.configure('TLabelframe', background=self.colors['bg'], bordercolor=self.colors['pending_text']); self.style.configure('TLabelframe.Label', background=self.colors['bg'], foreground=self.colors['text'], font=('Segoe UI', 10, 'bold')); self.style.configure('Cancel.TButton', background=self.colors['error'], foreground='white'); self.style.map('Cancel.TButton', background=[('active', '#A82A36')]); self.style.configure('Pause.TButton', background=self.colors['accent'], foreground='white'); self.style.map('Pause.TButton', background=[('active', '#005C9E')]); self.style.configure('Accent.TButton', background=self.colors['success'], foreground='white'); self.style.map('Accent.TButton', background=[('active', '#1E7E34')])
        self.style.configure('Add.TButton', background=self.colors['success'], foreground='white'); self.style.map('Add.TButton', background=[('active', '#1E7E34')])
        self.style.configure('Remove.TButton', background=self.colors['error'], foreground='white'); self.style.map('Remove.TButton', background=[('active', '#A82A36')])
    
    def _create_widgets(self):
        container = ttk.Frame(self.root); container.pack(fill='both', expand=True); self.canvas = tk.Canvas(container, bg=self.colors['bg'], highlightthickness=0); scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview); self.scrollable_frame = ttk.Frame(self.canvas); canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw"); self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))); self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(canvas_window, width=e.width)); self.root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units")); scrollbar.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True); main_frame = self.scrollable_frame
        top_controls = ttk.LabelFrame(main_frame, text="1. Topic & Script Settings", padding=10); top_controls.pack(fill="x", pady=5, padx=15); top_controls.columnconfigure(1, weight=1)
        self.topic_var = tk.StringVar(); self.language_var = tk.StringVar()
        ttk.Label(top_controls, text="Video Topic:", font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, padx=(5,10), pady=10, sticky="w")
        ttk.Entry(top_controls, textvariable=self.topic_var, font=('Segoe UI', 11)).grid(row=0, column=1, padx=5, sticky="ew")
        lang_combo = ttk.Combobox(top_controls, textvariable=self.language_var, values=SCRIPT_LANGUAGES, state="readonly", width=10)
        lang_combo.grid(row=0, column=2, padx=5)
        lang_combo.bind("<<ComboboxSelected>>", self.on_language_change)
        sug_btn_frame = ttk.Frame(top_controls); sug_btn_frame.grid(row=0, column=3, padx=5)
        self.suggest_topic_button = ttk.Button(sug_btn_frame, text="💡 Suggest Topic", command=self.fetch_topic); self.suggest_topic_button.pack(side="left")
        self.suggest_similar_button = ttk.Button(sug_btn_frame, text="🧠 Suggest Similar", command=self.fetch_similar_topic); self.suggest_similar_button.pack(side="left", padx=(5,0))
        
        script_frame = ttk.LabelFrame(main_frame, text="2. Review and Edit Script", padding=10); script_frame.pack(fill="x", pady=5, padx=15)
        script_frame.columnconfigure(0, weight=1)
        self.script_text = scrolledtext.ScrolledText(script_frame, wrap=tk.WORD, height=8, font=("Segoe UI", 10), state='disabled', background=self.colors['card'], foreground=self.colors['text'], insertbackground=self.colors['text'])
        self.script_text.grid(row=0, column=0, sticky='ew')
        
        self.toggle_script_button = ttk.Button(script_frame, text="✍️ Edit Voiceover Script", command=self.toggle_script_view)
        
        ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=10, padx=15)
        settings_container = ttk.Frame(main_frame); settings_container.pack(fill="x", padx=10); settings_container.columnconfigure(0, weight=1); settings_container.columnconfigure(1, weight=1); left_col, right_col = ttk.Frame(settings_container), ttk.Frame(settings_container); left_col.grid(row=0, column=0, sticky="nsew", padx=(5, 5)); right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 5))
        core_frame = ttk.LabelFrame(left_col, text="3. Video & Voice Settings", padding=10); core_frame.pack(fill="x", pady=5); core_frame.columnconfigure(1, weight=1); core_frame.columnconfigure(3, weight=1);
        ttk.Label(core_frame, text="AI Voice:").grid(row=0, column=0, padx=5, pady=5, sticky="w"); self.voice_var = tk.StringVar(); ttk.Combobox(core_frame, textvariable=self.voice_var, values=list(VOICES.keys()), state="readonly").grid(row=0, column=1, padx=5, sticky="ew")
        ttk.Label(core_frame, text="Aspect Ratio:").grid(row=1, column=0, padx=5, pady=5, sticky="w"); self.aspect_ratio_var = tk.StringVar(); ttk.Combobox(core_frame, textvariable=self.aspect_ratio_var, values=list(SDXL_DIMENSIONS.keys()), state="readonly").grid(row=1, column=1, padx=5, sticky="ew")
        
        ttk.Label(core_frame, text="Image Model:").grid(row=0, column=2, padx=(10,5), pady=5, sticky="w"); self.image_model_var = tk.StringVar();
        image_model_combo = ttk.Combobox(core_frame, textvariable=self.image_model_var, values=list(IMAGE_MODELS.keys()), state="readonly")
        image_model_combo.grid(row=0, column=3, padx=5, sticky="ew")

        subtitle_frame = ttk.LabelFrame(left_col, text="4. Subtitles & Style", padding=10); subtitle_frame.pack(fill="x", pady=5)
        subtitle_frame.columnconfigure(1, weight=1); subtitle_frame.columnconfigure(3, weight=1)
        self.font_var, self.position_var, self.color_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.border_var, self.size_var = tk.IntVar(), tk.IntVar()
        ttk.Label(subtitle_frame, text="Font:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Combobox(subtitle_frame, textvariable=self.font_var, values=SUBTITLE_FONTS, state="readonly", width=12).grid(row=0, column=1)
        ttk.Label(subtitle_frame, text="Size (%):").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        ttk.Spinbox(subtitle_frame, from_=1, to=20, textvariable=self.size_var, width=5).grid(row=0, column=3)
        ttk.Label(subtitle_frame, text="Position:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        ttk.Combobox(subtitle_frame, textvariable=self.position_var, values=list(SUBTITLE_POSITIONS.keys()), state="readonly", width=12).grid(row=1, column=1)
        ttk.Label(subtitle_frame, text="Color:").grid(row=1, column=2, padx=5, pady=5, sticky="w")
        ttk.Combobox(subtitle_frame, textvariable=self.color_var, values=SUBTITLE_COLORS, state="readonly", width=12).grid(row=1, column=3)
        ttk.Label(subtitle_frame, text="Border:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        ttk.Spinbox(subtitle_frame, from_=0, to=10, textvariable=self.border_var, width=5).grid(row=2, column=1)
        self.add_emojis_var = tk.BooleanVar(value=True)
        tk.Checkbutton(subtitle_frame, text="Add Emojis", variable=self.add_emojis_var, bg=self.colors['bg'], fg=self.colors['text'], activebackground=self.colors['bg'], activeforeground=self.colors['text'], selectcolor=self.colors['card']).grid(row=2, column=2, columnspan=2, sticky="w", padx=5)

        content_frame = ttk.LabelFrame(right_col, text="Branding & Music", padding=10); content_frame.pack(fill="x", pady=5); content_frame.columnconfigure(1, weight=1)
        
        self.verbose_logging_var = tk.BooleanVar(value=False)
        tk.Checkbutton(content_frame, text="Verbose Logging (Show Prompts)", variable=self.verbose_logging_var, bg=self.colors['bg'], fg=self.colors['text'], activebackground=self.colors['bg'], activeforeground=self.colors['text'], selectcolor=self.colors['card']).grid(row=0, column=0, columnspan=3, sticky="w", padx=5)

        self.intro_enabled_var = tk.BooleanVar(value=True); tk.Checkbutton(content_frame, text="Enable Video Intro", variable=self.intro_enabled_var, bg=self.colors['bg'], fg=self.colors['text'], activebackground=self.colors['bg'], activeforeground=self.colors['text'], selectcolor=self.colors['card']).grid(row=1, column=0, columnspan=3, sticky="w", padx=5)
        ttk.Label(content_frame, text="Channel Logo:").grid(row=2, column=0, sticky="w", padx=5, pady=2); self.logo_path_var = tk.StringVar(); ttk.Entry(content_frame, textvariable=self.logo_path_var).grid(row=2, column=1, sticky="ew", padx=5, pady=2); ttk.Button(content_frame, text="Browse...", command=lambda: self.select_file("logo", self.logo_path_var)).grid(row=2, column=2, padx=5, pady=2)
        ttk.Label(content_frame, text="Logo Opacity:").grid(row=3, column=0, sticky="w", padx=5, pady=5); self.logo_opacity_var = tk.DoubleVar(value=0.5); ttk.Scale(content_frame, from_=0.1, to=1.0, orient="horizontal", variable=self.logo_opacity_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=5)
        music_frame = ttk.LabelFrame(right_col, text=" ", padding=10); music_frame.pack(fill="x", pady=5); music_frame.columnconfigure(1, weight=1); self.bg_music_path_var, self.bg_music_vol_var = tk.StringVar(), tk.DoubleVar(value=0.2); ttk.Label(music_frame, text="BG Music File:").grid(row=0, column=0, sticky="w", padx=5); ttk.Entry(music_frame, textvariable=self.bg_music_path_var).grid(row=0, column=1, sticky="ew", padx=5); ttk.Button(music_frame, text="Browse", command=lambda: self.select_file("audio", self.bg_music_path_var)).grid(row=0, column=2, padx=2); ttk.Label(music_frame, text="Volume:").grid(row=1, column=0, sticky="w", padx=5, pady=5); ttk.Scale(music_frame, from_=0, to=1, orient="horizontal", variable=self.bg_music_vol_var).grid(row=1, column=1, columnspan=3, sticky="ew", padx=5)
        ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=10, padx=15)
        progress_frame = ttk.LabelFrame(main_frame, text="Progress", padding=10); progress_frame.pack(fill="x", pady=(5, 5), padx=15); self.progress_stepper = ProgressStepper(progress_frame, STAGES, self.colors); self.progress_stepper.pack(fill='x', expand=True, pady=5); self.timer_label = ttk.Label(progress_frame, text="Elapsed: 00:00", font=('Segoe UI', 9)); self.timer_label.pack(side='right', pady=(5,0))
        
        action_frame = ttk.Frame(main_frame, padding=(15,10,15,15)); action_frame.pack(fill='x')
        self.settings_button = ttk.Button(action_frame, text="⚙️ API Settings", command=self.open_settings); self.settings_button.pack(side="left", padx=5)
        
        self.one_click_mode_var = tk.BooleanVar(value=False)
        one_click_check = tk.Checkbutton(action_frame, text="🚀 One-Click Mode", variable=self.one_click_mode_var, bg=self.colors['bg'], fg=self.colors['text'], activebackground=self.colors['bg'], activeforeground=self.colors['text'], selectcolor=self.colors['card'], command=self.toggle_one_click_mode)
        one_click_check.pack(side='left', padx=10)

        self.upload_button = ttk.Button(action_frame, text="📤 Upload to YouTube", command=self.handle_upload, state="disabled")
        self.upload_button.pack(side="left", padx=10)

        self.cancel_button = ttk.Button(action_frame, text="❌ Cancel", command=self.cancel_creation, style="Cancel.TButton", state="disabled"); self.cancel_button.pack(side="right", padx=(5,0))
        self.pause_button = ttk.Button(action_frame, text="⏸️ Pause", command=self.toggle_pause, style="Pause.TButton", state="disabled"); self.pause_button.pack(side="right", padx=(5,0))
        
        self.main_action_button = ttk.Button(action_frame, text="1. Generate Script", command=self.handle_main_action)
        self.main_action_button.pack(side="right")
        
        log_frame = ttk.LabelFrame(main_frame, text="Progress Log", padding=10); log_frame.pack(fill="both", expand=True, pady=5, padx=15); self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state='disabled', font=("Courier New", 9), background=self.colors['card'], foreground=self.colors['text'], insertbackground=self.colors['text']); self.log_text.pack(fill="both", expand=True); self.log_queue = queue.Queue(); self.root.after(100, self.process_queue)

    def on_language_change(self, event=None):
        if self.language_var.get() in ["Hindi", "Bhai Lang"] and self.audio_script_cache:
            self.toggle_script_button.grid(row=0, column=1, sticky='ne', padx=5, pady=5)
        else:
            self.toggle_script_button.grid_remove()

    def toggle_script_view(self):
        if not self.audio_script_cache or not self.subtitle_script_cache:
            return
        
        current_text = self.script_text.get("1.0", tk.END).strip()
        self.script_text.config(state='normal')
        
        if self.script_box_mode == 'subtitle':
            self.subtitle_script_cache = current_text
            self.script_text.delete(1.0, tk.END)
            self.script_text.insert(tk.END, self.audio_script_cache)
            self.toggle_script_button.config(text="✍️ Edit Subtitle Script")
            self.script_box_mode = 'audio'
        else:
            self.audio_script_cache = current_text
            self.script_text.delete(1.0, tk.END)
            self.script_text.insert(tk.END, self.subtitle_script_cache)
            self.toggle_script_button.config(text="✍️ Edit Voiceover Script")
            self.script_box_mode = 'subtitle'
        
        if self.one_click_mode_var.get():
            self.script_text.config(state='disabled')

    def toggle_one_click_mode(self):
        if self.one_click_mode_var.get():
            self.main_action_button.config(text="🚀 Generate & Upload", style="Accent.TButton")
            self.script_text.config(state='disabled')
        else:
            self.main_action_button.config(text="1. Generate Script", style="TButton")
            if self.script_text.get("1.0", tk.END).strip():
                self.script_text.config(state='normal')
    
    def handle_main_action(self):
        if self.one_click_mode_var.get():
            self.start_one_click_generation()
        else:
            script_content = self.script_text.get("1.0", tk.END).strip()
            if not script_content:
                self.start_script_generation()
            else:
                self.start_video_creation()

    def handle_upload(self):
        if not self.last_video_path or not os.path.exists(self.last_video_path):
            messagebox.showerror("Error", "No valid video file found to upload.")
            return

        # Use a separate thread to not freeze the GUI
        threading.Thread(target=self.upload_worker, daemon=True).start()
        
    def upload_worker(self):
        self.log_queue.put(('stage_update', (7, 'in_progress')))
        if self.youtube_service is None:
            self.youtube_service = youtube_authenticate(self.log_queue)

        if not self.youtube_service:
            messagebox.showerror("Authentication Failed", "Could not authenticate with YouTube. Check logs.")
            self.log_queue.put(('stage_update', (7, 'error')))
            return

        script_for_metadata = self.subtitle_script_cache if self.subtitle_script_cache else ""
        title, desc, tags, _ = generate_youtube_metadata(self.config.get('GEMINI_API_KEYS', []), script_for_metadata, self.log_queue)
        
        if title is None:
            messagebox.showwarning("Metadata Failed", "Could not generate YouTube metadata. Please enter manually.")
            initial_data = {'title': self.topic_var.get(), 'description': '', 'tags': ''}
        else:
            initial_data = {'title': title, 'description': desc, 'tags': tags}
        
        # We need to schedule the dialog on the main thread
        self.root.after(0, lambda: self.show_upload_dialog(initial_data))

    def show_upload_dialog(self, initial_data):
        dialog = YouTubeUploadDialog(self.root, "Upload to YouTube", initial_data)
        if dialog.result:
            # After getting data, go back to a worker thread for the upload
            threading.Thread(target=self.perform_upload, args=(dialog.result,), daemon=True).start()
        else:
            self.log_queue.put(('log', "YouTube upload cancelled by user."))
            self.log_queue.put(('stage_update', (7, 'error')))
            
    def perform_upload(self, upload_data):
        upload_to_youtube(
            self.youtube_service,
            self.last_video_path,
            upload_data['title'],
            upload_data['description'],
            upload_data['tags'],
            self.log_queue
        )
        self.log_queue.put(('stage_update', (7, 'complete')))

    def _load_settings(self):
        self.language_var.set(self.config.get('last_language', 'English'))
        self.voice_var.set(self.config.get('last_voice_name', "Adam (Male, American)"))
        self.aspect_ratio_var.set(self.config.get('last_aspect_ratio', list(SDXL_DIMENSIONS.keys())[0]))
        self.intro_enabled_var.set(self.config.get('intro_on', True))
        self.logo_path_var.set(self.config.get('logo_path', ''))
        self.logo_opacity_var.set(self.config.get('logo_opacity', 0.5))
        self.font_var.set(self.config.get('last_font', 'Arial'))
        self.position_var.set(self.config.get('last_position', 'Bottom'))
        self.color_var.set(self.config.get('last_color', 'White'))
        self.border_var.set(self.config.get('last_border', 3))
        self.size_var.set(self.config.get('last_sub_size', 5))
        self.add_emojis_var.set(self.config.get('add_emojis', True))
        self.bg_music_path_var.set(self.config.get('bg_music_path', ''))
        self.bg_music_vol_var.set(self.config.get('bg_music_vol', 0.2))
        self.config['suggested_topic_history'] = self.config.get('suggested_topic_history', [])
        self.image_model_var.set(self.config.get('last_image_model', list(IMAGE_MODELS.keys())[0]))
        self.verbose_logging_var.set(self.config.get('verbose_logging', False))
        self.on_language_change()

    def _save_settings(self):
        self.config['last_language'] = self.language_var.get(); self.config['last_voice_name'] = self.voice_var.get(); self.config['last_aspect_ratio'] = self.aspect_ratio_var.get(); self.config['intro_on'] = self.intro_enabled_var.get(); self.config['logo_path'] = self.logo_path_var.get(); self.config['logo_opacity'] = self.logo_opacity_var.get(); self.config['last_font'] = self.font_var.get(); self.config['last_position'] = self.position_var.get(); self.config['last_color'] = self.color_var.get(); self.config['last_border'] = self.border_var.get(); self.config['last_sub_size'] = self.size_var.get(); self.config['add_emojis'] = self.add_emojis_var.get(); self.config['bg_music_path'] = self.bg_music_path_var.get(); self.config['bg_music_vol'] = self.bg_music_vol_var.get()
        self.config['suggested_topic_history'] = self.config.get('suggested_topic_history', [])
        self.config['last_image_model'] = self.image_model_var.get()
        self.config['verbose_logging'] = self.verbose_logging_var.get()
        save_config(self.config)

    def select_file(self, file_type, var):
        if file_type == "logo": path = filedialog.askopenfilename(title="Select Logo Image", filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
        elif file_type == "audio": path = filedialog.askopenfilename(title="Select Background Music", filetypes=[("Audio Files", "*.mp3 *.wav")])
        else: path = None
        if path: var.set(path)
    def open_settings(self):
        settings_win = SettingsWindow(self.root, self.config)
        self.root.wait_window(settings_win)
        self.config = load_config()
        self.log_queue.put(('log', "⚙️ Settings updated. Configuration reloaded."))
    
    def process_queue(self):
        try:
            while True:
                msg_type, data = self.log_queue.get_nowait()
                if msg_type == "log": self.log_text.config(state='normal'); self.log_text.insert(tk.END, data + '\n'); self.log_text.config(state='disabled'); self.log_text.see(tk.END)
                elif msg_type == "set_topic": self.topic_var.set(data)
                elif msg_type == "set_script": 
                    self.script_text.config(state='normal'); self.script_text.delete(1.0, tk.END); self.script_text.insert(tk.END, data); 
                    if not self.one_click_mode_var.get():
                        self.main_action_button.config(text="2. Create Video", style="Accent.TButton")
                elif msg_type == "show_image_review": self.show_image_review_window(data)
                elif msg_type == "stage_update": self.progress_stepper.update_stage(data[0], data[1])
                elif msg_type == "task_successful":
                    self.last_video_path = data
                    self._reset_ui_after_run(success=True)
                    if not self.one_click_mode_var.get():
                        if messagebox.askyesno("Success! 🎉", f"Video created successfully!\n\nDo you want to open the folder?"):
                            folder_path = os.path.dirname(data)
                            if sys.platform == "win32": os.startfile(folder_path)
                            else: subprocess.call(["open", "-R", folder_path] if sys.platform == "darwin" else ["xdg-open", folder_path])
                elif msg_type == "task_failed": self._reset_ui_after_run(success=False)
                elif msg_type == "reset_for_script_gen": self._reset_ui_after_run(is_script_gen_only=True)
        except queue.Empty: pass
        self.root.after(100, self.process_queue)

    def show_image_review_window(self, image_paths):
        review_window = tk.Toplevel(self.root); review_window.title("Review and Replace Images"); review_window.transient(self.root); review_window.grab_set(); main_frame = ttk.Frame(review_window, padding=10); main_frame.pack(fill="both", expand=True); canvas = tk.Canvas(main_frame); scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview); scrollable_frame = ttk.Frame(canvas); scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))); canvas.create_window((0, 0), window=scrollable_frame, anchor="nw"); canvas.configure(yscrollcommand=scrollbar.set); path_vars = []
        for i, path in enumerate(image_paths):
            row_frame = ttk.Frame(scrollable_frame, padding=5); row_frame.pack(fill='x', expand=True)
            try:
                img = PILImage.open(path); img.thumbnail((100, 100)); photo = ImageTk.PhotoImage(img); lbl = ttk.Label(row_frame, image=photo); lbl.image = photo; lbl.pack(side="left", padx=10)
            except Exception: lbl = ttk.Label(row_frame, text="Preview\nError", width=12); lbl.pack(side="left", padx=10)
            path_var = tk.StringVar(value=path); path_vars.append(path_var); entry = ttk.Entry(row_frame, textvariable=path_var, width=70); entry.pack(side="left", fill='x', expand=True, padx=10)
            def replace_image(var):
                new_path = filedialog.askopenfilename(title="Select new image", filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
                if new_path: var.set(new_path)
            btn = ttk.Button(row_frame, text="Replace...", command=lambda v=path_var: replace_image(v)); btn.pack(side="left", padx=10)
        canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        def on_continue(): self.final_image_paths = [var.get() for var in path_vars]; self.image_review_event.set(); review_window.destroy()
        continue_button = ttk.Button(main_frame, text="Continue Video Creation", command=on_continue, style="Accent.TButton"); continue_button.pack(pady=10)

    def update_timer(self):
        if hasattr(self, 'start_time') and self.start_time is not None and not self.pause_event.is_set(): elapsed = int(time.time() - self.start_time); self.timer_label.config(text=f"Elapsed: {elapsed//60:02d}:{elapsed%60:02d}")
        self.root.after(1000, self.update_timer)
    
    def fetch_topic(self):
        if not self.config.get('GEMINI_API_KEYS'): messagebox.showerror("Missing API Key", "Go to 'API Settings' and add at least one Google Gemini API key."); return
        self._toggle_ui_for_task(True); threading.Thread(target=self._fetch_topic_worker, daemon=True).start()
    
    def fetch_similar_topic(self):
        base_topic = self.topic_var.get().strip()
        if not base_topic: messagebox.showwarning("Input Needed", "Please type a topic or keyword in the box before suggesting similar topics."); return
        if not self.config.get('GEMINI_API_KEYS'): messagebox.showerror("Missing API Key", "Go to 'API Settings' and add at least one Google Gemini API key."); return
        self._toggle_ui_for_task(True); threading.Thread(target=self._fetch_similar_topic_worker, args=(base_topic,), daemon=True).start()

    def _toggle_ui_for_task(self, is_running, is_pausable=False):
        """Central function to control UI state."""
        state = 'disabled' if is_running else 'normal'
        self.suggest_topic_button.config(state=state)
        self.suggest_similar_button.config(state=state)
        self.main_action_button.config(state=state)
        self.settings_button.config(state=state)
        
        if is_pausable:
            self.pause_button.config(state='normal' if is_running else 'disabled')
            self.cancel_button.config(state='normal' if is_running else 'disabled')
        else:
            self.pause_button.config(state='disabled')
            self.cancel_button.config(state='disabled')

    def _fetch_topic_worker(self):
        try:
            gemini_keys = self.config.get('GEMINI_API_KEYS', [])
            topic, _ = get_trending_topic(gemini_keys, self.log_queue, self.config.get('suggested_topic_history', []))
            if topic: 
                self.log_queue.put(('set_topic', topic))
                self.config['suggested_topic_history'].append(topic)
                self._save_settings()
        finally: self.log_queue.put(("reset_for_script_gen", None))

    def _fetch_similar_topic_worker(self, base_topic):
        try:
            gemini_keys = self.config.get('GEMINI_API_KEYS', [])
            topic, _ = get_similar_topic(gemini_keys, base_topic, self.log_queue, self.config.get('suggested_topic_history', []))
            if topic: 
                self.log_queue.put(('set_topic', topic))
                self.config['suggested_topic_history'].append(topic)
                self._save_settings()
        finally: self.log_queue.put(("reset_for_script_gen", None))

    def toggle_pause(self):
        if not self.pause_event.is_set(): 
            self.pause_event.set(); self.pause_button.config(text="▶️ Resume"); self.log_queue.put(('log', "\n⏸️ Process Paused.")); self.pause_start_time = time.time()
        else:
            if hasattr(self, 'pause_start_time'): self.start_time += time.time() - self.pause_start_time; delattr(self, 'pause_start_time')
            self.pause_event.clear(); self.pause_button.config(text="⏸️ Pause"); self.log_queue.put(('log', "\n▶️ Process Resumed."))
    
    def cancel_creation(self):
        if messagebox.askyesno("Cancel? 🛑", "Are you sure you want to cancel the video creation?"): 
            self.log_queue.put(('log', "\n❌ Cancellation requested by user...")); 
            self.cancel_event.set();
            if self.pause_event.is_set(): # If paused, unpause to allow cancellation to proceed
                self.pause_event.clear()  
    
    def _check_for_pause_or_cancel(self):
        if self.cancel_event.is_set(): raise CancellationError("Video creation cancelled.")
        while self.pause_event.is_set():
            if self.cancel_event.is_set(): raise CancellationError("Cancelled while paused.")
            time.sleep(0.5)
            
    def _reset_ui_after_run(self, success=False, is_script_gen_only=False):
        self.start_time = None
        self._toggle_ui_for_task(is_running=False)
        
        self.upload_button.config(state='normal' if success else 'disabled')

        if not is_script_gen_only:
            self.script_text.config(state='disabled')
            self.script_text.delete(1.0, tk.END)
            self.main_action_button.config(text="1. Generate Script", style="TButton")
            self.audio_script_cache = None
            self.subtitle_script_cache = None
            self.on_language_change()
            if self.one_click_mode_var.get():
                self.main_action_button.config(text="🚀 Generate & Upload", style="Accent.TButton")

        self.pause_button.config(text="⏸️ Pause"); 

    def start_script_generation(self):
        if not self.topic_var.get().strip(): messagebox.showerror("Error", "Please enter a video topic."); return
        if not self.config.get('GEMINI_API_KEYS'): messagebox.showerror("Missing API Key", "Please add a Google Gemini API key in settings."); return
        self._toggle_ui_for_task(is_running=True); threading.Thread(target=self.script_generation_worker, daemon=True).start()
    
    def script_generation_worker(self):
        try:
            self.log_queue.put(('stage_update', (0, 'in_progress')))
            add_emojis = self.add_emojis_var.get()
            gemini_keys = self.config.get('GEMINI_API_KEYS', [])
            audio_script, subtitle_script, _ = generate_script(gemini_keys, self.topic_var.get().strip(), self.language_var.get(), self.log_queue, add_emojis=add_emojis)
            
            if audio_script:
                self.audio_script_cache = audio_script
                self.subtitle_script_cache = subtitle_script
                self.script_box_mode = 'subtitle'
                display_script = subtitle_script if subtitle_script else audio_script
                self.log_queue.put(('set_script', display_script))
                self.log_queue.put(('log', f"🎤 VOICEOVER SCRIPT (for non-English):\n---\n{self.audio_script_cache}\n---"))
                self.log_queue.put(('stage_update', (0, 'complete')))
                self.on_language_change()
                if self.topic_var.get() not in self.config['suggested_topic_history']:
                    self.config['suggested_topic_history'].append(self.topic_var.get())
                    self._save_settings()
            else:
                self.log_queue.put(('stage_update', (0, 'error')))
        finally:
            self.log_queue.put(("reset_for_script_gen", None))
    
    def start_video_creation(self):
        try:
            if self.script_box_mode == 'subtitle':
                self.subtitle_script_cache = self.script_text.get("1.0", tk.END).strip()
            else: 
                self.audio_script_cache = self.script_text.get("1.0", tk.END).strip()

            audio_script = self.audio_script_cache
            subtitle_script = self.subtitle_script_cache

            if not audio_script or not subtitle_script:
                messagebox.showerror("Error", "Could not find valid scripts. Please generate a script first.")
                self._reset_ui_after_run(); return

            if not self.config.get('CLOUDFLARE_ACCOUNTS') or not self.config.get('ELEVENLABS_API_KEYS'):
                messagebox.showerror("Missing API Key", "Please ensure Cloudflare and ElevenLabs keys are set.")
                self._reset_ui_after_run(); return
            
            self._save_settings()
            
            options = {
                "audio_script": audio_script, "subtitle_script": subtitle_script,
                "topic": self.topic_var.get().strip(), "language": self.language_var.get(), "voice_id": VOICES.get(self.voice_var.get()), "aspect_ratio_key": self.aspect_ratio_var.get(), "subtitle_options": {'font': self.font_var.get(), 'position': SUBTITLE_POSITIONS[self.position_var.get()], 'color': self.color_var.get(), 'border': self.border_var.get(), 'size': self.size_var.get()}, "intro_on": self.intro_enabled_var.get(), "logo_path": self.logo_path_var.get(), "logo_opacity": self.logo_opacity_var.get(), "bg_music_path": self.bg_music_path_var.get(), "bg_music_vol": self.bg_music_vol_var.get(), "image_model_name": self.image_model_var.get(), "verbose_logging": self.verbose_logging_var.get()
            }

            self.cancel_event.clear(); self.pause_event.clear(); 
            self._toggle_ui_for_task(is_running=True, is_pausable=True)
            self.progress_stepper.reset(); self.start_time = time.time(); self.update_timer(); self.log_queue.put(('stage_update', (0, 'complete'))); 
            threading.Thread(target=self.video_creation_worker, args=(options, False), daemon=True).start()
        
        except Exception as e:
            self.log_queue.put(('log', f"❌ UNEXPECTED ERROR on start: {e}\n{traceback.format_exc()}"))
            self._reset_ui_after_run()

    def start_one_click_generation(self):
        if not all(self.config.get(key) for key in ['GEMINI_API_KEYS', 'ELEVENLABS_API_KEYS', 'CLOUDFLARE_ACCOUNTS']):
            messagebox.showerror("API Keys Missing", "Please ensure all API keys (Gemini, ElevenLabs, Cloudflare) are set in the settings for One-Click mode.")
            return
        if not os.path.exists(CLIENT_SECRET_FILE):
             messagebox.showerror("YouTube Setup Missing", "client_secret.json not found. Please follow setup instructions for the YouTube upload feature.")
             return
        self._toggle_ui_for_task(is_running=True, is_pausable=True)
        threading.Thread(target=self.one_click_worker, daemon=True).start()

    def one_click_worker(self):
        try:
            self.progress_stepper.reset()
            self.cancel_event.clear(); self.pause_event.clear();
            self.start_time = time.time(); self.update_timer()

            gemini_keys = self.config.get('GEMINI_API_KEYS', [])
            topic, gemini_keys = get_trending_topic(gemini_keys, self.log_queue)
            if not topic: raise Exception("Failed to generate a topic.")
            self.log_queue.put(('set_topic', topic))

            add_emojis = self.add_emojis_var.get()
            audio_script, subtitle_script, _ = generate_script(gemini_keys, topic, self.language_var.get(), self.log_queue, add_emojis=add_emojis)
            if not audio_script: raise Exception("Failed to generate script.")
            self.log_queue.put(('set_script', subtitle_script))
            # Cache scripts for potential manual upload later
            self.audio_script_cache = audio_script
            self.subtitle_script_cache = subtitle_script

            options = {
                "audio_script": audio_script, "subtitle_script": subtitle_script,
                "topic": topic, "language": self.language_var.get(), "voice_id": VOICES.get(self.voice_var.get()), "aspect_ratio_key": self.aspect_ratio_var.get(), "subtitle_options": {'font': self.font_var.get(), 'position': SUBTITLE_POSITIONS[self.position_var.get()], 'color': self.color_var.get(), 'border': self.border_var.get(), 'size': self.size_var.get()}, "intro_on": self.intro_enabled_var.get(), "logo_path": self.logo_path_var.get(), "logo_opacity": self.logo_opacity_var.get(), "bg_music_path": self.bg_music_path_var.get(), "bg_music_vol": self.bg_music_vol_var.get(), "image_model_name": self.image_model_var.get(), "verbose_logging": self.verbose_logging_var.get()
            }
            
            # This is the full pipeline, calling the main worker with one_click=True
            self.video_creation_worker(options, one_click=True)

        except Exception as e:
            self.log_queue.put(('log', f"❌ A critical error occurred in One-Click mode: {e}\n{traceback.format_exc()}"))
            self.log_queue.put(("task_failed", None))

    def video_creation_worker(self, options, one_click=False):
        for directory in [OUTPUT_DIR, VOICEOVER_DIR, TEMP_DIR]: os.makedirs(directory, exist_ok=True)
        final_video_path = None; stage_idx = 1
        try:
            audio_script = options['audio_script']
            subtitle_script = options['subtitle_script']
            
            self.log_queue.put(('stage_update', (0, 'complete')))
            
            dimensions = SDXL_DIMENSIONS[options['aspect_ratio_key']]; options['target_size'] = (dimensions['width'], dimensions['height']); self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress'))); audio_file, _ = generate_voiceover(audio_script, self.config.get('ELEVENLABS_API_KEYS', []), options['voice_id'], options['topic'], self.log_queue);
            if not audio_file: raise Exception("Voiceover generation failed.")
            subtitle_data = generate_subtitles(audio_file, self.log_queue)
            if subtitle_data: subtitle_data = align_script_with_subtitles(subtitle_script, subtitle_data, self.log_queue)
            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1; self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress')))
            
            script_for_visuals = subtitle_script 
            gemini_keys = self.config.get('GEMINI_API_KEYS', [])
            if options['language'] not in ['English']:
                   self.log_queue.put(('log', f"  -> ℹ️ Translating script to English for visual prompt generation..."))
                   translation_prompt = f"Translate the following script into clear, descriptive English. Retain the core meaning and visual elements. Output only the translated English text:\n\n{subtitle_script}"
                   translated_script, gemini_keys = call_gemini_text_api(gemini_keys, None, translation_prompt, self.log_queue)
                   if translated_script:
                       script_for_visuals = translated_script
                       self.log_queue.put(('log', "  -> ✅ Translation successful."))
                   else:
                       self.log_queue.put(('log', "  -> ⚠️ Translation failed. Using original script for visuals."))
            
            visual_prompts, _ = generate_visual_prompts(gemini_keys, script_for_visuals, self.log_queue, self._check_for_pause_or_cancel, verbose=options['verbose_logging'])
            if not visual_prompts: raise Exception("Visual prompt generation failed.")
            
            image_model_id = IMAGE_MODELS[options['image_model_name']]
            image_files = generate_images_cloudflare(visual_prompts, self.config.get('CLOUDFLARE_ACCOUNTS', []), self.log_queue, image_model_id, self._check_for_pause_or_cancel)
            if not image_files: raise Exception("Image generation failed.")
            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1
            
            if one_click:
                self.final_image_paths = image_files
            else:
                self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress'))); self.log_queue.put(('show_image_review', image_files)); self.image_review_event.clear(); self.image_review_event.wait()
                if self.cancel_event.is_set(): raise CancellationError("Cancelled during review.")
                image_files = self.final_image_paths; self.log_queue.put(('log', "✅ Image review complete. Resuming..."))

            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1; self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress'))); intro_path = create_intro_video({**options, 'image_paths': image_files}, self.log_queue, self._check_for_pause_or_cancel) if options['intro_on'] else None
            if options['intro_on'] and not intro_path: self.log_queue.put(('log', "  -> ⚠️ WARNING: Intro video failed but continuing..."))
            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1; self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress'))); video_duration = AudioFileClip(audio_file).duration if audio_file else len(image_files) * 4; main_video_path = create_main_video({**options, 'image_paths': image_files, 'voiceover_path': audio_file, 'subtitle_data': subtitle_data, 'duration': video_duration}, self.log_queue, self._check_for_pause_or_cancel)
            if not main_video_path: raise Exception("Main video assembly failed.")
            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1; self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress'))); sanitized_topic = sanitize_filename(options['topic']); filename = f"{sanitized_topic.replace(' ', '_')[:50] or 'video'}_{int(time.time())}.mp4"; output_path = os.path.join(OUTPUT_DIR, filename); final_video_path = merge_videos(intro_path, main_video_path, output_path, {**options, 'intro_path': intro_path}, self.log_queue, self._check_for_pause_or_cancel)
            if not final_video_path: raise Exception("Final video merge failed.")
            
            self.log_queue.put(('stage_update', (stage_idx, 'complete'))); stage_idx += 1

            if one_click:
                self._check_for_pause_or_cancel(); self.log_queue.put(('stage_update', (stage_idx, 'in_progress')))
                title, desc, tags, _ = generate_youtube_metadata(self.config.get('GEMINI_API_KEYS', []), subtitle_script, self.log_queue)
                if title:
                    yt_service = youtube_authenticate(self.log_queue)
                    if yt_service:
                        upload_to_youtube(yt_service, final_video_path, title, desc, tags, self.log_queue)
                    else:
                        self.log_queue.put(('log', "  -> ⚠️ WARNING: YouTube authentication failed. Skipping upload."))
                self.log_queue.put(('stage_update', (stage_idx, 'complete')))
                messagebox.showinfo("One-Click Complete", f"Fully autonomous process finished!\n\nFinal video saved to:\n{final_video_path}")

            if final_video_path: self.log_queue.put(("task_successful", final_video_path))

        except CancellationError: 
            self.log_queue.put(('log', "\n🛑 Process was cancelled by the user.")); 
            self.log_queue.put(('stage_update', (stage_idx, 'error')))
            self.log_queue.put(("task_failed", None)) # Explicitly send fail message
        except Exception as e: 
            self.log_queue.put(('log', f"❌ A critical error occurred: {e}\n{traceback.format_exc()}")); 
            self.log_queue.put(('stage_update', (stage_idx, 'error')))
            self.log_queue.put(("task_failed", None)) # Explicitly send fail message

def check_ffmpeg():
    try: subprocess.run(["ffmpeg", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return True
    except (subprocess.CalledProcessError, FileNotFoundError): return False

if __name__ == '__main__':
    if not check_ffmpeg():
        root = tk.Tk(); root.withdraw(); messagebox.showerror("FFmpeg Not Found", "FFmpeg is required. Please download from ffmpeg.org and add its 'bin' directory to your system's PATH."); root.destroy()
    else:
        # Compatibility fix for Pillow versions
        if hasattr(PIL.Image, 'Resampling'):
            if not hasattr(PIL.Image, 'ANTIALIAS'): 
                PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
                
        root = tk.Tk(); app = VideoCreatorApp(root)
        if not os.path.exists(CONFIG_FILE): app.open_settings()
        root.mainloop()
