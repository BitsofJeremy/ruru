# slow-tts-weirding

A skill to drive WeirDing's TTS over HTTP.

## Usage

```bash
# Generate a short clip with a specific voice
agent://slow-tts-weirding/{"text": "Hello world", "voice_id": 42}
```

## Features

- POST `/api/tts/generate` with JSON body
- Returns job ID + audio URL on completion
- Supports all voices via slug-based lookup
- Handles errors and failures
- Uses the minimal JSON API surface

## Configuration

The skill expects WeirDing running at http://localhost:5151 (configurable via environment).

## Requirements

- WeirDing 0.0.3+ with the JSON API routes
- Python 3.8+
- HTTP client (requests)