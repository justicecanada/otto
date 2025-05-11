# For sample court cases: https://www.scc-csc.ca/cases-dossiers/hearings-audiences/webcasts-webdiffusions/

from collections import deque
import json
import re
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify
import os
import time
import subprocess
import threading
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
from langchain_text_splitters import TokenTextSplitter
from openai import AzureOpenAI

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from langchain_text_splitters import RecursiveCharacterTextSplitter


load_dotenv()
app = Flask(__name__)

# Configure maximum content length - adjust as needed
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB limit
app.config["UPLOAD_FOLDER"] = "uploads"

# Azure Translator configuration
translator_key = os.getenv("TRANSLATOR_KEY")
translator_region = os.getenv("TRANSLATOR_REGION") or "canadaeast"
translator_endpoint = (
    os.getenv("TRANSLATOR_ENDPOINT") or "https://api.cognitive.microsofttranslator.com/"
)

language_key = os.getenv("LANGUAGE_KEY")
language_endpoint = os.getenv("LANGUAGE_ENDPOINT")

# Azure configuration
speech_key = os.getenv("SPEECH_KEY")
speech_region = os.getenv("SPEECH_REGION")


# Ensure upload directory exists
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

azure_openai_client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version=os.getenv("AZURE_OPENAI_VERSION"),
)

deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")


@app.route("/generate-notes", methods=["POST"])
def generate_meeting_notes():
    try:
        data = request.get_json()
        transcript = data.get("cleaned_transcript")

        # Detect transcript language
        detected_lang = detect_language(transcript)

        # Get localized prompt
        localized_prompt = get_localized_prompt(detected_lang)

        # Generate notes with localized prompt
        notes = generate_structured_notes(transcript, localized_prompt)

        return jsonify({"notes": notes, "detected_language": detected_lang})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def detect_language(text: str) -> str:
    """Detect dominant language using Azure AI Language service"""
    credential = AzureKeyCredential(language_key)
    client = TextAnalyticsClient(language_endpoint, credential)

    MAX_LANG_DETECT_CHARS = 5120

    snippet = text[:MAX_LANG_DETECT_CHARS]
    try:
        response = client.detect_language(documents=[snippet], country_hint="ca")
        if not response[0].is_error:
            return response[0].primary_language.iso6391_name
    except Exception as e:
        print(f"Language detection error: {e}")

    return "en"  # Fallback to English


def get_localized_prompt(language_code: str) -> str:
    """Return localized prompt template based on detected language"""
    prompts = {
        "en": """Create comprehensive meeting notes from this cleaned transcript. Include:
- Key discussion points
- Action items and deadlines
- Decisions made with rationale
- Follow-up tasks
- Important quotes
- Include speaker attribution and action owner names where possible
Maintain chronological order and use markdown formatting with sections:
# Meeting Notes
## Key Points
## Action Items
## Decisions
## Follow-ups
## Notable Quotes""",
        "fr": """Créez un compte rendu détaillé à partir de cette transcription nettoyée. Inclure :
- Points clés de discussion
- Éléments actionnables avec échéances
- Décisions prises avec leur justification
- Tâches de suivi
- Citations importantes
- Inclure l'attribution des intervenants et les responsables des actions lorsque possible
Maintenez l'ordre chronologique et utilisez le formatage Markdown avec les sections :
# Compte Rendu
## Points Clés
## Actions
## Décisions
## Suivis
## Citations Remarquables""",
        "iu": """ᑲᑎᑭᓐᓂᖅᓴᐅᑎᐅᓂᖓ ᐃᓄᒃᑎᑐᑦ ᑎᑎᕋᖅᓯᒪᔪᖅ:
- ᐱᕙᓪᓕᐊᔪᑦ ᐅᖃᐅᓯᖏᑦ
- ᐊᑐᖅᑕᐅᔪᑦ ᐱᓕᕆᐊᖑᔪᑦ ᐅᓪᓗᒥᒃ
- ᑲᑎᑎᑦᓯᓂᖏᑦ ᐊᒻᒪᓗ ᑕᒪᒃᑯᐊ ᓇᓗᓇᐃᖅᐸᑦᑐᑦ
- ᐊᑐᖅᑕᐅᓂᐊᖅᑐᑦ ᐱᓕᕆᐊᖑᔪᑦ
- ᐅᖃᓕᒫᒐᓕᐅᖅᑎᑦᓯᓂᖏᑦ
- ᐅᖃᖅᑐᖅᑕᐅᔪᓄᑦ ᐊᒻᒪᓗ ᐱᓕᕆᖃᑎᒌᒃᑐᓄᑦ ᐊᑐᖅᑕᐅᓂᖏᓐᓂᑦ
ᐊᑐᓕᖅᑎᑦᓯᔨᒻᒪᕆᒃᑐᖅ ᐊᒻᒪᓗ markdown-ᒥᒃ ᐊᑐᓕᖅᑎᑦᓯᔨᒻᒪᕆᒃᑐᖅ:
# ᑲᑎᑭᓐᓂᖅᓴᐅᑎᑦ
## ᐱᕙᓪᓕᐊᔪᑦ ᐅᖃᐅᓯᖏᑦ
## ᐊᑐᖅᑕᐅᔪᑦ ᐱᓕᕆᐊᖑᔪᑦ
## ᑲᑎᑎᑦᓯᓂᖏᑦ
## ᐊᑐᖅᑕᐅᓂᐊᖅᑐᑦ ᐱᓕᕆᐊᖑᔪᑦ
## ᐅᖃᓕᒫᒐᓕᐅᖅᑎᑦᓯᓂᖏᑦ""",
    }
    return prompts.get(language_code, prompts["en"])


def generate_structured_notes(transcript: str, prompt: str) -> str:
    """Generate notes using dynamic prompt"""
    try:
        response = azure_openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript},
            ],
            temperature=0.3,
            max_tokens=4000,
            top_p=0.9,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"Notes generation failed: {e}")
        return "Could not generate meeting notes"


def generate_chunk_summary(chunk):
    """Generate concise chunk summary for context"""
    try:
        response = azure_openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": "Generate 1-2 sentence summary focusing on key entities and legal references",
                },
                {"role": "user", "content": chunk},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except:
        return ""


def clean_transcript_chunk(
    chunk, global_summary=None, previous_chunk=None, context_window=None
):
    """Enhanced cleaning with speaker name replacement and context integration"""
    # Build hierarchical context stack
    context_instructions = []

    # 1. Global summary context
    if global_summary:
        context_instructions.append(
            f"GLOBAL CASE CONTEXT (do not modify): {global_summary[:2000]}..."
        )

    # 2. Previous chunk continuity
    if previous_chunk:
        prev_summary = generate_chunk_summary(previous_chunk)
        context_instructions.append(
            f"PRIOR SEGMENT CONTINUITY (maintain in current chunk): {prev_summary[:1000]}..."
        )

    # 3. Recent context window
    if context_window:
        newline = "\n"
        context_instructions.append(
            f"RECENT CONTEXT WINDOW (preserve references):\n{newline.join(context_window)[:2000]}..."
        )

    system_prompt = f"""Professional Legal Transcript Editor with Hierarchical Context:

1. CONTEXT MANAGEMENT:
{' '.join(context_instructions) if context_instructions else 'No additional context'}

2. PROCESSING RULES:
- Maintain exact timestamp format [HH:MM:SS]
- Preserve legal terminology and exhibit references
- Never invent speaker names
- Mark uncertain names with (?) suffix"""

    try:
        # Get base cleaned text
        response = azure_openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"""
Clean and enhance this transcript chunk:
{chunk}

Apply these transformations:
1. Fix punctuation/capitalization
2. Remove filler words (um, uh)
3. Expand legal abbreviations
4. Maintain speaker label consistency""",
                },
            ],
            temperature=0.1,
            max_tokens=16000,
        )
        cleaned = response.choices[0].message.content.strip()

        return cleaned

    except Exception as e:
        print(f"Cleaning error: {e}")
        return chunk


@app.route("/cleanup", methods=["POST"])
def handle_cleanup():
    try:
        data = request.json
        transcript_text = data.get("transcript_text")
        if not transcript_text:
            return jsonify({"error": "No transcript text provided"}), 400

        # Generate initial global summary
        global_summary = generate_summary(transcript_text)

        # Configure hierarchical splitting
        splitter = TokenTextSplitter.from_tiktoken_encoder(
            chunk_size=12000,
            chunk_overlap=3000,
            encoding_name="cl100k_base",
        )

        chunks = splitter.split_text(transcript_text)
        cleaned_chunks = []
        context_window = deque(maxlen=3)
        previous_cleaned = None

        for chunk in chunks:
            # Generate dynamic context from window
            dynamic_context = "\n".join(context_window) if context_window else ""

            # Clean chunk with current speaker map
            cleaned = clean_transcript_chunk(
                chunk,
                global_summary=global_summary,
                previous_chunk=previous_cleaned,
                context_window=dynamic_context,
            )

            # Update tracking variables
            cleaned_chunks.append(cleaned)
            context_window.append(cleaned)
            previous_cleaned = cleaned

        full_cleaned = "\n\n".join(cleaned_chunks)

        return jsonify(
            {
                "cleaned_transcript": full_cleaned,
                "summary": global_summary,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def generate_summary(transcript):
    """Generate initial conversation summary"""
    try:
        response = azure_openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": "Generate a concise summary of this transcript focusing on key points, themes, and important details. Do not include timestamps or speaker labels. If any speakers are unnamed (i.e. Guest-1, Guest-2), create an index of probable speaker names based on the conversation.",
                },
                {"role": "user", "content": transcript},
            ],
            temperature=0.2,
            max_tokens=16000,
        )
        return response.choices[0].message.content.strip()
    except:
        return "Summary unavailable"


def convert_to_wav(input_path):
    """
    Convert audio/video file to 16kHz mono WAV format
    For video files, this will extract the audio track
    """
    # Get file extension to determine if it's a video or audio file
    file_ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(input_path)[0] + "_converted.wav"

    try:
        # Common ffmpeg command options for both audio and video
        cmd_options = [
            "-y",  # Overwrite output file if exists
            "-vn",  # No video (extract audio only)
            "-acodec",  # Audio codec
            "pcm_s16le",  # Linear PCM 16-bit
            "-ar",  # Audio rate
            "16000",  # 16kHz
            "-ac",  # Audio channels
            "1",  # Mono
            output_path,  # Output file
        ]

        # Build the ffmpeg command
        cmd = ["ffmpeg", "-i", input_path] + cmd_options

        # Run the conversion
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e}")
        return None
    except Exception as e:
        print(f"Conversion error: {e}")
        return None


def transcribe_audio(file_path, transcript_path):
    """Perform audio transcription using Azure Speech Service and write to file"""
    if not speech_key or not speech_region:
        return [{"error": "Azure Speech credentials not configured"}]

    config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    config.speech_recognition_language = "en-US"
    config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults, "true"
    )

    audio_config = speechsdk.audio.AudioConfig(filename=file_path)
    transcriber = speechsdk.transcription.ConversationTranscriber(config, audio_config)

    results = []
    done = threading.Event()

    # Open transcript file for writing
    transcript_file = open(transcript_path, "w", encoding="utf-8")

    def handle_transcription(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            offset = evt.result.offset / 10000000  # Convert to seconds
            timestamp = time.strftime("%H:%M:%S", time.gmtime(offset))
            speaker = evt.result.speaker_id or "Unknown"
            text = evt.result.text

            # Add to results array
            results.append(
                {
                    "timestamp": timestamp,
                    "speaker": speaker,
                    "text": text,
                }
            )

            # Write to transcript file as it happens
            message = f"[{timestamp}]: {speaker}\n{text}\n\n"
            transcript_file.write(message)
            transcript_file.flush()  # Ensure it's written immediately

            print(message)

    def handle_session_stop(evt):
        transcript_file.close()
        done.set()

    def handle_cancellation(evt):
        transcript_file.close()
        done.set()

    transcriber.transcribed.connect(handle_transcription)
    transcriber.session_stopped.connect(handle_session_stop)
    transcriber.canceled.connect(handle_cancellation)

    transcriber.start_transcribing_async()
    done.wait()

    return results


def send_from_directory(directory, filename):
    """Serve files from a directory"""
    try:
        return send_from_directory(directory, filename)
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def handle_upload():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Empty filename"}), 400

        # Save uploaded file
        filename = file.filename
        temp_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(temp_path)

        # Get file type (audio or video)
        file_type = "video" if file.mimetype.startswith("video/") else "audio"

        # Create transcript file path with original filename + .txt
        transcript_path = f"{temp_path}.txt"

        # Convert to WAV if needed
        wav_path = convert_to_wav(temp_path)
        if not wav_path:
            return jsonify({"error": "Failed to convert file"}), 500

        # Perform transcription
        transcript = transcribe_audio(wav_path, transcript_path)

        # Cleanup temporary files
        if os.path.exists(wav_path) and wav_path != temp_path:
            os.remove(wav_path)

        # Keep the original file for playback

        return jsonify(
            {
                "transcript": transcript,
                "transcript_file": transcript_path,
                "original_file": filename,
                "file_type": file_type,
                "file_url": f"/uploads/{filename}",  # URL for media playback
            }
        )

    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({"error": str(e)}), 500


# Serve files from upload folder
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# 413 Error handler
@app.errorhandler(413)
def request_entity_too_large(error):
    print("413 error triggered")
    return (
        jsonify(
            {
                "error": "File too large",
                "message": "The file exceeds the maximum allowed size.",
                "max_size_mb": app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024),
            }
        ),
        413,
    )


@app.route("/translate", methods=["POST"])
def handle_translation():
    try:
        data = request.json
        transcript_text = data.get("transcript_text")
        target_language = data.get("target_language")

        if not transcript_text:
            return jsonify({"error": "No transcript text provided"}), 400

        if not target_language:
            return jsonify({"error": "No target language specified"}), 400

        # Check if API key is available
        if not translator_key:
            return jsonify({"error": "Translator API key not configured"}), 500

        translated_text = translate_transcript(transcript_text, target_language)

        return jsonify(
            {
                "translated_transcript": translated_text,
                "target_language": target_language,
            }
        )

    except Exception as e:
        print(f"Translation error: {str(e)}")
        return jsonify({"error": str(e)}), 500


def parse_html_to_transcript(translated_html):
    """Convert translated HTML back to original transcript format"""
    transcript_entries = []

    # Use BeautifulSoup for HTML parsing
    soup = BeautifulSoup(translated_html, "html.parser")
    for entry in soup.find_all(class_="transcript-entry"):
        timestamp = entry.find(class_="timestamp").text.strip()
        speaker = entry.find(class_="speaker").text.strip()
        content = entry.find(class_="translatable-content").text.strip()

        transcript_entries.append(f"{timestamp} {speaker}\n{content}")

    return "\n\n".join(transcript_entries)


def translate_transcript(text, target_language):
    """Translate transcript with chunking for Azure request limits"""
    # Convert transcript to HTML format with protected elements
    html_content = convert_transcript_to_html(text)

    credential = AzureKeyCredential(translator_key)
    client = TextTranslationClient(credential=credential, region=translator_region)

    # Use CHARACTER-based splitting instead of token-based
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=45000,  # 45k buffer for HTML tags
        chunk_overlap=1000,
        separators=["\n\n", "\n", " "],  # Prefer splitting at transcript boundaries
    )

    chunks = splitter.split_text(html_content)
    translated_chunks = []

    for chunk in chunks:
        try:
            # Execute translation with HTML handling
            response = client.translate(
                body=[chunk], to_language=[target_language], text_type="html"
            )
            translated_chunks.append(response[0].translations[0].text)
        except Exception as e:
            print(f"Translation chunk failed: {str(e)}")
            translated_chunks.append(chunk)  # Add untranslated chunk as fallback

    # Reconstruct translated transcript from chunks
    full_translated = "".join(translated_chunks)

    # Convert HTML back to transcript format
    return parse_html_to_transcript(full_translated)


def convert_transcript_to_html(transcript_text):
    """Wrap transcript elements in protective HTML tags"""
    entries = transcript_text.split("\n\n")
    html_lines = []

    for entry in entries:
        lines = entry.strip().split("\n")
        if len(lines) >= 2:
            # Extract timestamp and speaker
            header_match = re.match(r"(\[[0-9:]+\]:\s*)(.*)", lines[0])
            if header_match:
                timestamp = header_match.group(1)
                speaker = header_match.group(2)
                content = "\n".join(lines[1:])

                # Wrap protected elements
                html_entry = f"""
                <div class="transcript-entry">
                    <span class="notranslate timestamp">{timestamp}</span>
                    <span class="notranslate speaker">{speaker}</span>
                    <div class="translatable-content">{content}</div>
                </div>
                """
                html_lines.append(html_entry)
    return "\n".join(html_lines)


# Generic error handler for all other errors
@app.errorhandler(Exception)
def handle_exception(e):
    print(f"Unhandled exception: {e}")
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
