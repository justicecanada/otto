import json
import os
from collections import deque

from django.http import JsonResponse
from django.shortcuts import render

from langchain_text_splitters import TokenTextSplitter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.utils.decorators import app_access_required, budget_required

from .utils import (
    clean_transcript_chunk,
    convert_to_wav,
    detect_language,
    generate_structured_notes,
    generate_summary,
    get_localized_prompt,
    transcribe_audio,
    translate_transcript,
    translator_key,
)

app_name = "transcriber"
logger = get_logger(__name__)

os.makedirs("uploads", exist_ok=True)


@app_access_required(app_name)
def index(request):
    bind_contextvars(feature="transcriber")
    return render(
        request,
        "transcriber.html",
    )


@app_access_required(app_name)
@budget_required
def handle_cleanup(request):
    bind_contextvars(feature="transcriber")
    try:
        data = json.loads(request.body.decode("utf-8"))
        transcript_text = data.get("transcript_text")
        if not transcript_text:
            return JsonResponse({"error": "No transcript text provided"}, status=400)

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

        return JsonResponse(
            {
                "cleaned_transcript": full_cleaned,
                "summary": global_summary,
            }
        )

    except Exception as e:
        return JsonResponse(
            {"error": str(e)},
            status=500,
        )


@app_access_required(app_name)
@budget_required
def generate_meeting_notes(request):
    bind_contextvars(feature="transcriber")
    try:
        data = json.loads(request.body.decode("utf-8"))
        transcript = data.get("cleaned_transcript")
        # Detect transcript language
        detected_lang = detect_language(transcript)

        # Get localized prompt
        localized_prompt = get_localized_prompt(detected_lang)

        # Generate notes with localized prompt
        notes = generate_structured_notes(transcript, localized_prompt)

        return JsonResponse(
            {
                "notes": notes,
                "detected_language": detected_lang,
            }
        )

    except Exception as e:
        return JsonResponse(
            {"error": str(e)},
            status=500,
        )


@app_access_required(app_name)
@budget_required
def handle_translation(request):
    bind_contextvars(feature="transcriber")
    try:
        data = json.loads(request.body.decode("utf-8"))
        transcript_text = data.get("transcript_text")
        target_language = data.get("target_language")

        if not transcript_text:
            return JsonResponse({"error": "No transcript text provided"}, status=400)

        if not target_language:
            return JsonResponse({"error": "No target language specified"}, status=400)

        # Check if API key is available
        if not translator_key:
            return JsonResponse(
                {"error": "Translator API key is not configured"}, status=500
            )

        translated_text = translate_transcript(transcript_text, target_language)

        response = JsonResponse(
            {
                "translated_transcript": translated_text,
                "target_language": target_language,
            },
            status=200,
        )

        return response
    except json.JSONDecodeError as e:
        return JsonResponse(
            {
                "status": "error",
                "message": "Invalid JSON data provided.",
                "error": str(e),
            },
            status=400,
        )


@app_access_required(app_name)
@budget_required
def handle_upload(request):
    bind_contextvars(feature="transcriber")
    try:
        if "file" not in request.FILES:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        file = request.FILES.get("file")
        if file.name == "":
            return JsonResponse({"error": "Empty filename"}, status=400)

        # Save uploaded file
        filename = file.name
        temp_path = os.path.join("uploads", filename)
        with open(temp_path, "wb+") as destination:
            for chunk in file.chunks():
                destination.write(chunk)

        # Get file type (audio or video)
        file_type = "video" if file.content_type.startswith("video/") else "audio"

        # Create transcript file path with original filename + .txt
        transcript_path = f"{temp_path}.txt"

        # Convert to WAV if needed
        wav_path = convert_to_wav(temp_path)
        if not wav_path:
            return JsonResponse({"error": "Failed to convert file"}, status=500)

        # Perform transcription
        transcript = transcribe_audio(wav_path, transcript_path)

        # Cleanup temporary files
        if os.path.exists(wav_path) and wav_path != temp_path:
            os.remove(wav_path)

        # Keep the original file for playback

        return JsonResponse(
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
        return JsonResponse(
            {
                "error": str(e),
            },
            status=500,
        )
