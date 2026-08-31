# SAIA MCP Server

An MCP (Model Context Protocol) server that provides access to GWDG's Scientific AI Accelerator (SAIA) API. This enables AI agents to use various AI models for chat, embeddings, image generation, audio transcription, and document processing.

## Features

- **Chat Completion**: Multi-turn conversations with LLMs (Llama, Qwen, Mistral, DeepSeek, etc.)
- **Text Completion**: Direct text continuation
- **Embeddings**: Vector representations for semantic search (E5-Mistral, Multilingual-E5)
- **Vision Models**: Image understanding with VL models (Qwen-VL, InternVL, Gemma-3)
- **Image Generation**: Text-to-image with FLUX.1-schnell
- **Image Editing**: Image-to-image with Qwen-Image-Edit
- **Audio Transcription**: Speech-to-text with Whisper
- **Audio Translation**: Translate audio to English
- **Document Conversion**: PDF to markdown/HTML/JSON with Docling

## Dynamic Model Discovery

The server automatically:
1. Fetches available models from the SAIA `/models` endpoint
2. Includes known models from documentation (audio, image, embedding models)
3. Can parse documentation content to discover new models

Since the `/models` endpoint only returns chat completion models, the server maintains a registry of known models for other tasks (embeddings, audio, images) based on the SAIA documentation.

## Installation

```bash
# Clone or download this repository
cd saia-mcp

# Install with pip
pip install -e .

# Or with uv
uv pip install -e .
```

## Configuration

### Environment Variables

Set your SAIA API key:

```bash
export SAIA_API_KEY="your-api-key-here"
```

Or create a `.env` file:

```env
SAIA_API_KEY=your-api-key-here
SAIA_BASE_URL=https://chat-ai.academiccloud.de/v1
SAIA_MODEL_CACHE_TTL=3600
SAIA_DEFAULT_MAX_TOKENS=1024
SAIA_DEFAULT_TEMPERATURE=0.7
SAIA_REQUEST_TIMEOUT=120
```

### Getting an API Key

1. Go to [KISSKI LLM Service](https://kisski.gwdg.de/en/leistungen/2-02-llm-service)
2. Request an API key using your AcademicCloud email
3. Keys are personal and should not be shared

## Usage with Claude Code

Add to your Claude Code MCP configuration (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "saia": {
      "command": "saia-mcp",
      "env": {
        "SAIA_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Or if running from source:

```json
{
  "mcpServers": {
    "saia": {
      "command": "python",
      "args": ["-m", "saia_mcp.server"],
      "cwd": "/path/to/saia-mcp/src",
      "env": {
        "SAIA_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Available Tools

### `saia_list_models`
List available models, optionally filtered by capability.

```json
{
  "capability": "chat",  // Optional: chat, embeddings, vision, image_generation, audio_transcription
  "refresh": false       // Force refresh from API
}
```

### `saia_chat`
Send chat completion requests.

```json
{
  "model": "llama-3.3-70b-instruct",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "max_tokens": 1024,
  "temperature": 0.7
}
```

### `saia_completion`
Text completion from a prompt.

```json
{
  "model": "llama-3.3-70b-instruct",
  "prompt": "Once upon a time",
  "max_tokens": 512
}
```

### `saia_embeddings`
Generate embeddings for text.

```json
{
  "model": "e5-mistral-7b-instruct",
  "input": "Hello, world!",
  "encoding_format": "float"
}
```

### `saia_chat_with_vision`
Chat with images using vision models.

```json
{
  "model": "qwen2.5-vl-72b-instruct",
  "messages": [{"role": "user", "content": "What's in this image?"}],
  "images": ["/path/to/image.jpg"],
  "max_tokens": 1024
}
```

### `saia_image_generate`
Generate images from text.

```json
{
  "prompt": "A beautiful sunset over mountains",
  "n": 1,
  "size": "1024x1024"
}
```

### `saia_image_edit`
Edit images with text instructions.

```json
{
  "image": "/path/to/image.jpg",
  "prompt": "Make the sky more blue"
}
```

### `saia_transcribe`
Transcribe audio to text.

```json
{
  "audio_file": "/path/to/audio.mp3",
  "language": "en",
  "response_format": "text"
}
```

### `saia_translate_audio`
Translate audio to English.

```json
{
  "audio_file": "/path/to/german_audio.mp3",
  "response_format": "text"
}
```

### `saia_convert_document`
Convert PDF documents.

```json
{
  "document": "/path/to/document.pdf",
  "output_format": "markdown"
}
```

### `saia_update_models_from_docs`
Parse documentation to discover new models.

```json
{
  "doc_content": "<html>...documentation content...</html>"
}
```

## Available Models

### Chat/Completion Models
- `meta-llama-3.1-8b-instruct`
- `meta-llama-3.1-70b-instruct`
- `llama-3.3-70b-instruct`
- `qwen3-235b-a22b` (Reasoning)
- `qwq-32b` (Reasoning)
- `deepseek-r1` (Reasoning)
- `qwen2.5-coder-32b-instruct` (Code)
- `codestral-22b` (Code)
- `mistral-large-instruct-2411`

### Vision Models
- `qwen2.5-vl-72b-instruct`
- `internvl2.5-8b`
- `gemma-3-27b-it`

### Embedding Models
- `e5-mistral-7b-instruct`
- `multilingual-e5-large-instruct`

### Image Models
- `flux.1-schnell` (Text-to-image)
- `qwen-image-edit` (Image-to-image)

### Audio Models
- `whisper-large-v2` (Transcription & Translation)

## Rate Limits

The SAIA API has rate limits. Response headers include:
- `X-RateLimit-Limit-*`: Limits per minute/hour/day
- `X-RateLimit-Remaining-*`: Remaining quota
- `X-RateLimit-Reset-*`: Reset times

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Format
ruff format src/
```

## License

MIT License

## Links

- [SAIA Documentation](https://docs.hpc.gwdg.de/services/saia/)
- [KISSKI LLM Service](https://kisski.gwdg.de/en/leistungen/2-02-llm-service)
- [MCP Specification](https://modelcontextprotocol.io/)
