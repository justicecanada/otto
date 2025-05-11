# Otto Transcription Service

> **⚠️ IMPORTANT: PROOF OF CONCEPT ONLY ⚠️**  
> This codebase represents a **proof of concept** implementation and is not production-ready. This branch should **NEVER** be merged to main. It is intended for demonstration and exploration purposes only.

Otto Transcription Service is an AI-powered solution designed to transform audio and video files into accurate, editable transcripts with additional analysis capabilities. This tool leverages advanced speech recognition and AI models for advanced text processing, all wrapped in a user-friendly web application.

## Features

- **Media Upload**: Support for various audio and video file formats
- **Automated Transcription**: Azure Speech Service-powered transcription with speaker diarization
- **Human-in-the-Loop Editing**: Tools for reviewing and correcting transcripts
- **Advanced Analysis**:
  - Transcript cleaning and normalization
  - Summarization
  - Meeting notes generation
  - Context-aware processing
- **Background Processing**: Asynchronous handling of transcription tasks
- **Seamless Integration**: Designed to be integrated into the Otto platform

## Prerequisites

- Python 3.11 or newer
- FFmpeg (for audio/video conversion)
- Azure Cognitive Services Speech account
- Azure OpenAI account with appropriate model deployment
- Adequate disk space for temporary file storage

## Installation

### Create and activate a virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Set up your environment variables

Create a `.env` file in the root directory with the following variables:

```
# Azure Speech Service
SPEECH_KEY=your_azure_speech_service_key
SPEECH_REGION=your_azure_speech_service_region

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_KEY=your_azure_openai_key
AZURE_OPENAI_VERSION=2023-12-01-preview
AZURE_OPENAI_DEPLOYMENT_NAME=your_model_deployment_name
```

### Install FFmpeg

FFmpeg is required for audio/video conversion:

**Ubuntu/Debian**:
```bash
sudo apt update
sudo apt install ffmpeg
```

**Windows**:
Download from [FFmpeg.org](https://ffmpeg.org/download.html) and add to your PATH.

## Usage

### Starting the application

```bash
python app.py
```

The application will be available at http://localhost:5000

### Debugging the application

Launch VS Code and open the `poc` folder. Use the built-in debugger to set breakpoints and inspect variables.
You can also run the application in debug mode from the terminal:

```bash
python -m flask run --debug
```

## Contributing

As this is a proof-of-concept only, contributions should be focused on exploration and demonstration. Please do not attempt to merge this branch to main.

If you'd like to experiment with the code:

1. Create a new branch from this one
2. Make your changes
3. Document your modifications and findings
4. Perform a pull request against this branch for review
