"""SAIA MCP Server - Provides access to GWDG's Scientific AI Accelerator API.

Refactored to use FastMCP for cleaner tool definitions.
"""

import asyncio
import logging
from typing import Any, Optional

from fastmcp import FastMCP

from .client import ModelCapability, SAIAClient
from .config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP(
    name="saia-mcp",
    instructions=(
        "SAIA MCP Server for GWDG's Scientific AI Accelerator API. "
        "Provides access to LLMs, embeddings, vision models, image generation, "
        "audio transcription/translation, and document conversion."
    )
)

# Global client instance
_client: SAIAClient | None = None


def get_client() -> SAIAClient:
    """Get or create SAIA client."""
    global _client
    if _client is None:
        _client = SAIAClient()
    return _client


# ============================================================================
# MCP Tools
# ============================================================================

@mcp.tool()
async def saia_list_models(
    capability: Optional[str] = None,
    refresh: bool = False
) -> dict[str, Any]:
    """List available models from the SAIA API.

    Returns model IDs and their capabilities. Can filter by capability type.

    Args:
        capability: Filter models by capability. Options: chat, completion,
                   embeddings, vision, image_generation, image_edit,
                   audio_transcription, audio_translation
        refresh: Force refresh model list from API (default: false)

    Returns:
        Dict with models list and count.
    """
    client = get_client()

    if refresh:
        await client.refresh_models(force=True)
    else:
        await client.refresh_models()

    cap_enum = None
    if capability:
        try:
            cap_enum = ModelCapability(capability)
        except ValueError:
            valid = [c.value for c in ModelCapability]
            return {"error": f"Invalid capability '{capability}'. Valid options: {valid}"}

    models = client.list_models(cap_enum)

    return {
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "capabilities": [c.value for c in m.capabilities],
                "context_length": m.context_length,
                "description": m.description,
            }
            for m in models
        ],
        "count": len(models),
    }


@mcp.tool()
async def saia_chat(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None
) -> dict[str, Any]:
    """Send a chat completion request to SAIA.

    Supports multi-turn conversations with various LLM models including
    Llama, Qwen, Mistral, DeepSeek, and more.

    Args:
        model: Model ID to use (e.g., 'llama-3.3-70b-instruct', 'qwen3-235b-a22b', 'deepseek-r1')
        messages: Array of message objects with 'role' (system/user/assistant) and 'content'
        max_tokens: Maximum tokens to generate (default: 1024)
        temperature: Sampling temperature (0-2)
        top_p: Nucleus sampling parameter (0-1)

    Returns:
        Dict with response content, model used, and token usage.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.chat_completion(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    choices = response.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        # Handle reasoning models that use 'reasoning_content' instead of 'content'
        content = message.get("content") or message.get("reasoning_content") or ""
        result = {
            "response": content,
            "model": response.get("model"),
            "usage": response.get("usage"),
        }
        # Include reasoning if present (for reasoning models)
        if message.get("reasoning"):
            result["reasoning"] = message.get("reasoning")
        return result
    return response


@mcp.tool()
async def saia_completion(
    model: str,
    prompt: str,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None
) -> dict[str, Any]:
    """Send a text completion request to SAIA.

    Provides text continuation from a given prompt.

    Args:
        model: Model ID to use
        prompt: Text prompt to complete
        max_tokens: Maximum tokens to generate (default: 1024)
        temperature: Sampling temperature

    Returns:
        Dict with completed text, model used, and token usage.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.completion(
        model=model,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    choices = response.get("choices", [])
    if choices:
        return {
            "text": choices[0].get("text", ""),
            "model": response.get("model"),
            "usage": response.get("usage"),
        }
    return response


@mcp.tool()
async def saia_embeddings(
    model: str,
    input: str | list[str],
    encoding_format: str = "float"
) -> dict[str, Any]:
    """Generate embeddings (vector representations) for text.

    Useful for semantic search, clustering, and similarity comparisons.
    Available models: e5-mistral-7b-instruct, multilingual-e5-large-instruct

    Args:
        model: Embedding model ID (e.g., 'e5-mistral-7b-instruct', 'multilingual-e5-large-instruct')
        input: Text or array of texts to embed
        encoding_format: Output format ('float' or 'base64', default: 'float')

    Returns:
        Dict with embeddings array, model used, and token usage.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.embeddings(
        model=model,
        input_text=input,
        encoding_format=encoding_format,
    )

    data = response.get("data", [])
    return {
        "embeddings": [item.get("embedding") for item in data],
        "model": response.get("model"),
        "usage": response.get("usage"),
    }


@mcp.tool()
async def saia_chat_with_vision(
    model: str,
    messages: list[dict[str, str]],
    images: list[str] | None = None,
    max_tokens: Optional[int] = None
) -> dict[str, Any]:
    """Chat with a vision-capable model, including images in the conversation.

    Supports models like qwen2.5-vl-72b-instruct, internvl2.5-8b, gemma-3-27b-it.

    Args:
        model: Vision model ID (e.g., 'qwen2.5-vl-72b-instruct', 'internvl2.5-8b')
        messages: Array of message objects with 'role' and 'content'
        images: Array of image file paths or base64-encoded image data
        max_tokens: Maximum tokens to generate (default: 1024)

    Returns:
        Dict with response content, model used, and token usage.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.chat_with_vision(
        model=model,
        messages=messages,
        images=images,  # type: ignore[arg-type]  # MCP passes strings, client accepts str|bytes
        max_tokens=max_tokens,
    )

    choices = response.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        # Handle reasoning models that use 'reasoning_content' instead of 'content'
        content = message.get("content") or message.get("reasoning_content") or ""
        result = {
            "response": content,
            "model": response.get("model"),
            "usage": response.get("usage"),
        }
        if message.get("reasoning"):
            result["reasoning"] = message.get("reasoning")
        return result
    return response


@mcp.tool()
async def saia_image_generate(
    prompt: str,
    n: int = 1,
    size: str = "1024x1024"
) -> dict[str, Any]:
    """Generate images from text descriptions using FLUX model.

    Supports various sizes and multiple image generation.

    Args:
        prompt: Text description of the image to generate
        n: Number of images to generate (1-4, default: 1)
        size: Image size ('256x256', '512x512', '1024x1024', default: '1024x1024')

    Returns:
        Dict with generated images (base64 or URL) and creation timestamp.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.image_generation(
        prompt=prompt,
        n=n,
        size=size,
    )

    return {
        "images": [
            {
                "b64_json": img.get("b64_json"),
                "url": img.get("url"),
            }
            for img in response.get("data", [])
        ],
        "created": response.get("created"),
    }


@mcp.tool()
async def saia_image_edit(
    image: str,
    prompt: str
) -> dict[str, Any]:
    """Edit an existing image using text instructions with Qwen-Image-Edit model.

    Supports semantic editing and appearance modifications.

    Args:
        image: Path to image file or base64-encoded image data
        prompt: Edit instructions describing desired changes

    Returns:
        Dict with edited images (base64 or URL) and creation timestamp.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.image_edit(
        image=image,
        prompt=prompt,
    )

    return {
        "images": [
            {
                "b64_json": img.get("b64_json"),
                "url": img.get("url"),
            }
            for img in response.get("data", [])
        ],
        "created": response.get("created"),
    }


@mcp.tool()
async def saia_transcribe(
    audio_file: str,
    language: Optional[str] = None,
    response_format: str = "text"
) -> dict[str, Any]:
    """Transcribe audio to text using Whisper model.

    Supports wav, mp3, mp4, flac formats up to 500MB.

    Args:
        audio_file: Path to audio file
        language: Language code (e.g., 'en', 'de'). Auto-detected if not specified.
        response_format: Output format ('text', 'srt', 'vtt', default: 'text')

    Returns:
        Dict with transcribed text.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.audio_transcription(
        audio_file=audio_file,
        language=language,
        response_format=response_format,
    )

    return response


@mcp.tool()
async def saia_translate_audio(
    audio_file: str,
    response_format: str = "text"
) -> dict[str, Any]:
    """Translate audio in any language to English text using Whisper model.

    Args:
        audio_file: Path to audio file
        response_format: Output format ('text', 'srt', 'vtt', default: 'text')

    Returns:
        Dict with translated English text.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.audio_translation(
        audio_file=audio_file,
        response_format=response_format,
    )

    return response


@mcp.tool()
async def saia_convert_document(
    document: str,
    output_format: str = "markdown"
) -> dict[str, Any]:
    """Convert PDF documents to markdown, HTML, or JSON using Docling.

    Supports table extraction and image handling.

    Args:
        document: Path to PDF document
        output_format: Output format ('markdown', 'html', 'json', default: 'markdown')

    Returns:
        Dict with converted document content.
    """
    client = get_client()
    await client.refresh_models()

    response = await client.document_conversion(
        document=document,
        output_format=output_format,
    )

    return response


@mcp.tool()
async def saia_update_models_from_docs(
    doc_content: str
) -> dict[str, Any]:
    """Parse documentation content to discover additional models.

    Useful for finding specialized models like image generation or audio models
    that may not be listed in the /models API endpoint.

    Args:
        doc_content: Documentation content (HTML or text) to parse for model information

    Returns:
        Dict with newly discovered models and total model count.
    """
    client = get_client()

    new_models = await client.update_models_from_docs(doc_content)

    return {
        "new_models_discovered": new_models,
        "count": len(new_models),
        "total_models": len(client.model_registry.models),
    }


@mcp.tool()
async def saia_get_rate_limits(
    warning_threshold: float = 20.0
) -> dict[str, Any]:
    """Get current rate limit status from the SAIA API.

    Shows remaining requests for minute/hour/day windows and warns when
    approaching limits. Rate limits are updated from response headers on
    every API call. Use this to monitor usage and avoid hitting rate limits.

    Args:
        warning_threshold: Percentage threshold for warnings (default: 20).
                          Returns a warning if remaining requests are below this percentage.

    Returns:
        Dict with rate limit info, warning if applicable, and has_data flag.
    """
    client = get_client()

    rate_info = client.rate_limits.to_dict()
    warning = client.rate_limits.get_warning(warning_threshold)

    result = {
        "rate_limits": rate_info,
        "has_data": bool(rate_info),
    }

    if warning:
        result["warning"] = warning

    if not rate_info:
        result["message"] = (
            "No rate limit data available yet. "
            "Rate limits are captured from API response headers. "
            "Make an API call first to populate this data."
        )

    return result


# ============================================================================
# Main Entry Point
# ============================================================================

async def init_client() -> None:
    """Initialize client and load models on startup."""
    if not settings.saia_api_key:
        logger.warning(
            "SAIA_API_KEY not set. Set it via environment variable or .env file."
        )

    client = get_client()
    await client.refresh_models()
    logger.info(f"SAIA MCP Server ready with {len(client.model_registry.models)} models")


def main() -> None:
    """Main entry point."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="SAIA MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport mode: stdio (default) or sse"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "9200")),
        help="Port for SSE transport (default: 9200)"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="Host for SSE transport (default: 127.0.0.1)"
    )

    args = parser.parse_args()

    # Initialize client before running server
    asyncio.run(init_client())

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port, show_banner=False)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
