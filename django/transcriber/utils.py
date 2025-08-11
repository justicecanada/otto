import json
import os
import re
import subprocess
import threading
import time
from io import BytesIO
from itertools import groupby
from urllib.parse import urlparse

from django.conf import settings
from django.core.files import File
from django.core.files.uploadedfile import InMemoryUploadedFile

import azure.cognitiveservices.speech as speechsdk
import ffmpeg
import requests
from azure.ai.textanalytics import TextAnalyticsClient
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import AzureOpenAI

# from transcriber.tasks import transcribe_audio_task

# Azure Translator configuration
translator_key = settings.AZURE_COGNITIVE_SERVICE_KEY
translator_region = settings.AZURE_COGNITIVE_SERVICE_REGION
translator_endpoint = settings.AZURE_COGNITIVE_SERVICE_ENDPOINT

language_key = settings.AZURE_COGNITIVE_SERVICE_KEY
language_endpoint = settings.AZURE_COGNITIVE_SERVICE_ENDPOINT

# Azure configuration
speech_key = settings.AZURE_COGNITIVE_SERVICE_KEY
speech_region = settings.AZURE_COGNITIVE_SERVICE_REGION

azure_openai_client = AzureOpenAI(
    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    api_key=settings.AZURE_OPENAI_KEY,
    api_version=settings.AZURE_OPENAI_VERSION,
)

deployment = settings.DEFAULT_TRANSCRIBER_MODEL


def detect_language(text: str) -> str:
    """
    Detect dominant language using Azure AI Language service.
    """

    credentials = AzureKeyCredential(language_key)
    endpoint = language_endpoint
    client = TextAnalyticsClient(endpoint=endpoint, credential=credentials)

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
        "en": """Create comprehensive meeting notes from this cleaned transcript, without using prior knowledge. Include:
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
            ## Notable Quotes

            Include timestamps (e.g. [00:00:45]) for all relevant information and quotations.
            Use only information, events, and names from the provided transcript. Under no circumstances should you reference any information or knowledge outside of the provided transcript.""",
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
    if not transcript:
        return Exception
    try:
        response = azure_openai_client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript},
            ],
            temperature=0.1,
            max_completion_tokens=4000,
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
    - Maintain exact timestamp format and values [HH:MM:SS]. Never change timestamps.
    - Preserve legal terminology and exhibit references
    - Never invent or change speaker names
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
    config.speech_recognition_language = "fr-CA"
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
        return {"error": "File not found"}, 404


def parse_html_to_transcript(translated_html):
    """Convert translated HTML back to original transcript format"""
    transcript_entries = []

    # Use BeautifulSoup for HTML parsing
    soup = BeautifulSoup(translated_html, "html.parser")
    for entry in soup.find_all(class_="transcript-line"):
        timestamp = entry.find(class_="timestamp").text.strip()
        speaker = entry.find(class_="speaker").text.strip()
        content = entry.find(class_="text").text.strip()

        transcript_entries.append(f"{timestamp} {speaker}\n{content}")

    return "\n\n".join(transcript_entries)


def translate_transcript(text, target_language):
    """Translate transcript with chunking for Azure request limits"""
    # Convert transcript to HTML format with protected elements
    html_content = convert_transcript_to_html(text)

    credential = AzureKeyCredential(translator_key)
    client = TextTranslationClient(
        credential=credential, region=settings.AZURE_COGNITIVE_SERVICE_REGION
    )

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


def convert_transcript_to_html(transcript_text, consolidate_sentences=False):
    """Wrap transcript elements in protective HTML tags"""
    entries = transcript_text.split("\n\n")
    line_dicts = []

    for entry in entries:
        lines = entry.strip().split("\n")
        if len(lines) >= 2:
            # Extract timestamp and speaker
            header_match = re.match(r"(\[[0-9:]+\]:\s*)(.*)", lines[0])
            if header_match:
                timestamp = header_match.group(1).strip(" :")
                speaker = header_match.group(2)
                content = "\n".join(lines[1:])

                line_dicts.append(
                    {"timestamp": timestamp, "speaker": speaker, "content": content}
                )
    if consolidate_sentences:
        line_dicts = combine_sentences(line_dicts)

    for line_dict in line_dicts:
        # Wrap protected elements
        line_dict[
            "html_entry"
        ] = f"""
                <div class="transcript-line">
                    <span class="notranslate timestamp" data-time={line_dict["timestamp"].strip("[]")}>{line_dict["timestamp"]}:</span>
                    <span class="notranslate speaker">{line_dict["speaker"]}</span>
                    <div class="text">{line_dict["content"]}</div>
                </div>
                """
    return "\n\n".join(x["html_entry"] for x in line_dicts)


def combine_sentences(line_dicts):
    speaker_groups = groupby(line_dicts, key=lambda x: x["speaker"])
    combined_entries = []
    for speaker, group in speaker_groups:
        group_list = list(group)
        # Group together elements where "content" is part of the same sentence (ends with ellipses)
        combined = []
        i = 0
        while i < len(group_list):
            entry = group_list[i]
            content = entry["content"]
            timestamp = entry["timestamp"]
            # Start concatenation if content ends with ellipses
            while content.rstrip().endswith("...") and i + 1 < len(group_list):
                next_entry = group_list[i + 1]
                next_entry["content"] = next_entry["content"].lstrip()
                # Concatenate with a space (or newline if you prefer)
                content = (
                    content.rstrip("... ")
                    + " "
                    + next_entry["content"][0].lower()
                    + next_entry["content"][1:]
                )
                i += 1
            combined.append(
                {"speaker": speaker, "timestamp": timestamp, "content": content}
            )
            i += 1
        combined_entries.extend(combined)
    return combined_entries


def get_video_from_parlvu(url, output_path="temp"):
    website = urlparse(url).netloc
    if website != "parlvu.parl.gc.ca":
        return "Invalid URL"

    parlvu_html = BeautifulSoup(
        requests.get(url).content,
        "html.parser",
    )
    script_element = parlvu_html.find(
        lambda tag: tag.name == "script" and "var availableStreams" in tag.text
    )
    available_streams = json.loads(
        re.search("(?<=var availableStreams = ).*?(?=\;)", script_element.text)[0]
    )
    floor_video_sd = next(
        (
            x
            for x in available_streams
            if x["Lang"] == "fl" and not (x["AudioOnly"] or x["IsHd"])
        ),
        None,
    )
    video_url = floor_video_sd["Url"]

    temp_file_path = f"{output_path}.mp4"

    ffmpeg.input(video_url).output(temp_file_path, c="copy").run(overwrite_output=True)

    with open(temp_file_path, "rb") as f:
        byte_stream = BytesIO(f.read())
        size = byte_stream.getbuffer().nbytes
        return InMemoryUploadedFile(
            byte_stream,
            field_name=None,
            name=temp_file_path,
            content_type="video/mp4",
            size=size,
            charset=None,
        )

    # return f"{output_path}.mp4"  # TODO: implement some kind of file management/cleanup capabilities
    # return f"{output_path}.mp4"  # TODO: implement some kind of file management/cleanup capabilities
