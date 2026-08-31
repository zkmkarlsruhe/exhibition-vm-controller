"""SAIA API client for interacting with GWDG's Scientific AI Accelerator."""

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class ModelCapability(str, Enum):
    """Model capabilities."""

    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDINGS = "embeddings"
    VISION = "vision"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDIT = "image_edit"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    AUDIO_TRANSLATION = "audio_translation"


@dataclass
class RateLimitInfo:
    """Rate limit information from API response headers."""

    limit_minute: int | None = None
    limit_hour: int | None = None
    limit_day: int | None = None
    remaining_minute: int | None = None
    remaining_hour: int | None = None
    remaining_day: int | None = None
    reset_seconds: int | None = None
    last_updated: float = 0

    def update_from_headers(self, headers: dict[str, str]) -> None:
        """Update rate limit info from response headers.

        Uses conservative approach but detects window resets:
        - During active window: keep the lowest remaining value seen
        - On window reset: accept new (higher) values from fresh window
        """
        now = time.time()

        # Parse limit values (these are maximums, take them as-is)
        new_limit_minute = _parse_int_header(headers.get("x-ratelimit-limit-minute"))
        new_limit_hour = _parse_int_header(headers.get("x-ratelimit-limit-hour"))
        new_limit_day = _parse_int_header(headers.get("x-ratelimit-limit-day"))

        # Parse remaining values
        new_remaining_minute = _parse_int_header(headers.get("x-ratelimit-remaining-minute"))
        new_remaining_hour = _parse_int_header(headers.get("x-ratelimit-remaining-hour"))
        new_remaining_day = _parse_int_header(headers.get("x-ratelimit-remaining-day"))

        # Parse reset time
        new_reset = _parse_int_header(headers.get("ratelimit-reset"))

        # Update limits (take the value if we have one)
        if new_limit_minute is not None:
            self.limit_minute = new_limit_minute
        if new_limit_hour is not None:
            self.limit_hour = new_limit_hour
        if new_limit_day is not None:
            self.limit_day = new_limit_day

        # Update remaining values with window reset detection
        # If new remaining > current remaining, a window reset likely occurred
        # In that case, accept the new (higher) value
        if new_remaining_minute is not None:
            if self.remaining_minute is None:
                self.remaining_minute = new_remaining_minute
            elif new_remaining_minute > self.remaining_minute:
                # Window reset detected - accept new higher value
                self.remaining_minute = new_remaining_minute
            else:
                # Same window - keep conservative (lower) value
                self.remaining_minute = min(self.remaining_minute, new_remaining_minute)

        if new_remaining_hour is not None:
            if self.remaining_hour is None:
                self.remaining_hour = new_remaining_hour
            elif new_remaining_hour > self.remaining_hour:
                # Window reset detected
                self.remaining_hour = new_remaining_hour
            else:
                self.remaining_hour = min(self.remaining_hour, new_remaining_hour)

        if new_remaining_day is not None:
            if self.remaining_day is None:
                self.remaining_day = new_remaining_day
            elif new_remaining_day > self.remaining_day:
                # Window reset detected
                self.remaining_day = new_remaining_day
            else:
                self.remaining_day = min(self.remaining_day, new_remaining_day)

        if new_reset is not None:
            self.reset_seconds = new_reset

        self.last_updated = now

    def get_warning(self, threshold_percent: float = 20.0) -> str | None:
        """Get warning message if any rate limit is below threshold.

        Args:
            threshold_percent: Warn if remaining is below this percentage.

        Returns:
            Warning message or None if all limits are healthy.
        """
        warnings = []

        if self.limit_minute and self.remaining_minute is not None:
            pct = (self.remaining_minute / self.limit_minute) * 100
            if pct < threshold_percent:
                warnings.append(f"minute: {self.remaining_minute}/{self.limit_minute} ({pct:.1f}%)")

        if self.limit_hour and self.remaining_hour is not None:
            pct = (self.remaining_hour / self.limit_hour) * 100
            if pct < threshold_percent:
                warnings.append(f"hour: {self.remaining_hour}/{self.limit_hour} ({pct:.1f}%)")

        if self.limit_day and self.remaining_day is not None:
            pct = (self.remaining_day / self.limit_day) * 100
            if pct < threshold_percent:
                warnings.append(f"day: {self.remaining_day}/{self.limit_day} ({pct:.1f}%)")

        if warnings:
            return f"Rate limit warning - low remaining: {', '.join(warnings)}"
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {}

        if self.limit_minute is not None:
            result["limit_minute"] = self.limit_minute
        if self.limit_hour is not None:
            result["limit_hour"] = self.limit_hour
        if self.limit_day is not None:
            result["limit_day"] = self.limit_day
        if self.remaining_minute is not None:
            result["remaining_minute"] = self.remaining_minute
        if self.remaining_hour is not None:
            result["remaining_hour"] = self.remaining_hour
        if self.remaining_day is not None:
            result["remaining_day"] = self.remaining_day
        if self.reset_seconds is not None:
            result["reset_seconds"] = self.reset_seconds
        if self.last_updated:
            result["last_updated"] = self.last_updated

        # Add percentage remaining for convenience
        if self.limit_minute and self.remaining_minute is not None:
            pct = (self.remaining_minute / self.limit_minute) * 100
            result["percent_remaining_minute"] = round(pct, 1)
        if self.limit_hour and self.remaining_hour is not None:
            pct = (self.remaining_hour / self.limit_hour) * 100
            result["percent_remaining_hour"] = round(pct, 1)
        if self.limit_day and self.remaining_day is not None:
            pct = (self.remaining_day / self.limit_day) * 100
            result["percent_remaining_day"] = round(pct, 1)

        return result


def _parse_int_header(value: str | None) -> int | None:
    """Parse an integer from a header value."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


@dataclass
class ModelInfo:
    """Information about a model."""

    id: str
    name: str
    capabilities: list[ModelCapability] = field(default_factory=list)
    context_length: int | None = None
    description: str = ""


@dataclass
class ModelRegistry:
    """Registry of available models with their capabilities."""

    models: dict[str, ModelInfo] = field(default_factory=dict)
    last_updated: float = 0

    # Known models with detailed descriptions for agent guidance
    # Descriptions explain what each model is best suited for
    KNOWN_MODELS: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        # =======================================================================
        # GENERAL PURPOSE CHAT MODELS
        # =======================================================================
        "llama-3.3-70b-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Meta's flagship open model (Dec 2024). Best for: general chat, "
                "multilingual dialogue (EN/DE/FR/IT/PT/ES/HI/TH), instruction following, "
                "and synthetic data generation. 128K context. Matches 405B performance "
                "at lower cost. Use for everyday tasks."
            ),
        },
        "meta-llama-3.1-70b-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Predecessor to 3.3. Solid general-purpose model for chat, summarization, and "
                "content generation. 128K context. Use when 3.3 unavailable or for comparison."
            ),
        },
        "meta-llama-3.1-8b-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Lightweight Llama model. Best for: fast responses, simple Q&A, basic chat, "
                "and low-latency applications. Good starting point before scaling up."
            ),
        },
        "mistral-large-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Mistral's large instruct model. Strong at instruction following, reasoning, "
                "and multilingual tasks. Good alternative to Llama for general chat."
            ),
        },
        # =======================================================================
        # REASONING MODELS (for math, logic, complex problem-solving)
        # =======================================================================
        "deepseek-r1": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "DeepSeek's reasoning model (Jan 2025). BEST FOR: complex math "
                "(97.3% MATH-500), competitive programming (2029 Elo Codeforces), "
                "STEM problems, and multi-step reasoning. Shows chain-of-thought "
                "in <think> tags. Use for hard problems requiring verification."
            ),
        },
        "qwen3-235b-a22b": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Qwen3 flagship MoE model (235B params, 22B active). Supports dual modes: "
                "'thinking mode' for complex reasoning/math/coding, 'non-thinking' for fast chat. "
                "119 languages. Best for: hard math, coding competitions, agent tasks. "
                "Rivals DeepSeek-R1 and GPT-4o. 128K context."
            ),
        },
        "qwq-32b": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Qwen's compact reasoning model. Best for: mathematical reasoning, "
                "coding challenges, and complex problem-solving. Matches DeepSeek-R1 "
                "performance at 1/20th the size. Include 'reason step by step' in "
                "prompts. Good for resource-constrained reasoning."
            ),
        },
        # =======================================================================
        # CODE-SPECIALIZED MODELS
        # =======================================================================
        "qwen2.5-coder-32b-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "State-of-the-art open-source code model. Best for: code generation "
                "(88% HumanEval), code completion, debugging, refactoring, and code "
                "review. Supports 92+ languages including Python, JS, Java, C++, Rust, "
                "Go. 128K context for large codebases. Matches GPT-4o on coding "
                "benchmarks. RECOMMENDED for code tasks."
            ),
        },
        "codestral-22b": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.COMPLETION],
            "description": (
                "Mistral's code model. Best for: code completion, fill-in-the-middle "
                "(IDE integration), code generation, and test writing. Supports 80+ "
                "languages. 32K context. Strong at single-file tasks. Use for "
                "IDE-style autocomplete scenarios."
            ),
        },
        # =======================================================================
        # VISION-LANGUAGE MODELS (for images, documents, charts)
        # =======================================================================
        "qwen2.5-vl-72b-instruct": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.VISION],
            "description": (
                "Qwen's flagship vision model. BEST FOR: document understanding "
                "(96.4 DocVQA), chart/diagram analysis, OCR, image Q&A, and video "
                "comprehension (1+ hour). Can localize objects with bounding boxes. "
                "Extracts structured data from invoices/forms. Matches GPT-4o on "
                "vision tasks. RECOMMENDED for document/image analysis."
            ),
        },
        "internvl2.5-8b": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.VISION],
            "description": (
                "Efficient vision-language model. Best for: OCR, document understanding, "
                "chart analysis, image captioning, and visual Q&A. Strong multilingual "
                "OCR. Rivals GPT-4o on benchmarks with 10x less training data. Good "
                "balance of quality and speed for vision tasks."
            ),
        },
        "gemma-3-27b-it": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.VISION],
            "description": (
                "Google's multimodal model. Best for: image understanding, visual Q&A, "
                "document OCR, creative writing, and multilingual chat (140+ languages). "
                "128K context. Runs on single GPU. Good for general vision+language tasks."
            ),
        },
        "medgemma-27b-it": {
            "capabilities": [ModelCapability.CHAT, ModelCapability.VISION],
            "description": (
                "Google's medical AI model. Best for: medical text comprehension, "
                "clinical reasoning, medical image analysis (radiology, pathology, "
                "dermatology), and healthcare Q&A. 87.7% on MedQA. FOR RESEARCH ONLY "
                "- not for direct clinical diagnosis. Use for medical literature "
                "review, education, and research prototyping."
            ),
        },
        # =======================================================================
        # EMBEDDING MODELS (for search, RAG, similarity)
        # =======================================================================
        "e5-mistral-7b-instruct": {
            "capabilities": [ModelCapability.EMBEDDINGS],
            "description": (
                "High-quality English embedding model. Best for: semantic search, RAG retrieval, "
                "document clustering, and reranking. 4096 dimensions, 4K token context. "
                "Instruction-aware (customizable via prompts). RECOMMENDED for English-only RAG. "
                "Add 'query: ' prefix for queries, 'passage: ' for documents."
            ),
        },
        "multilingual-e5-large-instruct": {
            "capabilities": [ModelCapability.EMBEDDINGS],
            "description": (
                "Multilingual embedding model. Best for: cross-lingual search, multilingual RAG, "
                "and semantic similarity across 100+ languages. 1024 dimensions, 512 token limit. "
                "RECOMMENDED for non-English or mixed-language content. "
                "Add 'query: ' and 'passage: ' prefixes."
            ),
        },
        "qwen3-embedding-4b": {
            "capabilities": [ModelCapability.EMBEDDINGS],
            "description": (
                "Qwen's embedding model. 2560 dimensions. Good alternative for semantic search "
                "and RAG. Balanced size/quality tradeoff."
            ),
        },
        # =======================================================================
        # IMAGE GENERATION & EDITING
        # =======================================================================
        "flux": {
            "capabilities": [ModelCapability.IMAGE_GENERATION],
            "description": (
                "FLUX.1-schnell text-to-image model. Best for: fast image generation (1-4 steps), "
                "creative illustrations, concept art, and visual content. Supports various aspect "
                "ratios (0.1-2.0 MP). Can render text in images. Open-source (Apache 2.0). "
                "Good prompt adherence. Use for quick visual prototyping."
            ),
        },
        "qwen-image-edit": {
            "capabilities": [ModelCapability.IMAGE_EDIT],
            "description": (
                "Image editing model. Best for: modifying existing images via text prompts, "
                "style transfer, object modifications, and appearance changes. "
                "Input an image + edit instruction to get modified result."
            ),
        },
        # =======================================================================
        # AUDIO MODELS
        # =======================================================================
        "whisper-large-v2": {
            "capabilities": [
                ModelCapability.AUDIO_TRANSCRIPTION,
                ModelCapability.AUDIO_TRANSLATION,
            ],
            "description": (
                "OpenAI's speech recognition model. Best for: audio transcription (99 languages), "
                "speech-to-text, and translation to English. Handles accents, background noise, "
                "and technical language well. Supports wav/mp3/mp4/flac up to 500MB. "
                "Use transcription for same-language, translation for any-to-English."
            ),
        },
    })

    def get_models_by_capability(self, capability: ModelCapability) -> list[ModelInfo]:
        """Get all models with a specific capability."""
        return [m for m in self.models.values() if capability in m.capabilities]


class SAIAClient:
    """Client for SAIA API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        audio_base_url: str | None = None,
    ):
        """Initialize SAIA client.

        Args:
            api_key: SAIA API key. Defaults to settings.saia_api_key.
            base_url: API base URL. Defaults to settings.saia_base_url.
            audio_base_url: Audio API base URL. Defaults to settings.saia_audio_base_url.
        """
        self.api_key = api_key or settings.saia_api_key
        self.base_url = (base_url or settings.saia_base_url).rstrip("/")
        self.audio_base_url = (audio_base_url or settings.saia_audio_base_url).rstrip("/")
        self.model_registry = ModelRegistry()
        self.rate_limits = RateLimitInfo()
        self._http_client: httpx.AsyncClient | None = None
        self._audio_http_client: httpx.AsyncClient | None = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(settings.request_timeout),
            )
        return self._http_client

    @property
    def audio_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for audio endpoints."""
        if self._audio_http_client is None or self._audio_http_client.is_closed:
            self._audio_http_client = httpx.AsyncClient(
                base_url=self.audio_base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=httpx.Timeout(settings.request_timeout),
            )
        return self._audio_http_client

    async def close(self) -> None:
        """Close HTTP clients."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
        if self._audio_http_client and not self._audio_http_client.is_closed:
            await self._audio_http_client.aclose()
            self._audio_http_client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make API request.

        Args:
            method: HTTP method.
            endpoint: API endpoint.
            **kwargs: Additional request arguments.

        Returns:
            Response JSON.

        Raises:
            httpx.HTTPStatusError: If request fails.
        """
        response = await self.http_client.request(method, endpoint, **kwargs)
        response.raise_for_status()

        # Update rate limits from response headers
        self.rate_limits.update_from_headers(dict(response.headers))

        return response.json()

    async def refresh_models(self, force: bool = False) -> None:
        """Refresh model registry from API and known models.

        Args:
            force: Force refresh even if cache is valid.
        """
        now = time.time()
        if not force and (now - self.model_registry.last_updated) < settings.model_cache_ttl:
            return

        logger.info("Refreshing model registry...")

        # Start with known models from documentation
        for model_id, info in self.model_registry.KNOWN_MODELS.items():
            self.model_registry.models[model_id] = ModelInfo(
                id=model_id,
                name=model_id,
                capabilities=info.get("capabilities", []),
                description=info.get("description", ""),
            )

        # Fetch models from API (these are typically chat models)
        try:
            data = await self._request("GET", "/models")
            api_models = data.get("data", [])

            for model in api_models:
                model_id = model.get("id", "")
                if not model_id:
                    continue

                # If already in registry, update with API data
                if model_id in self.model_registry.models:
                    existing = self.model_registry.models[model_id]
                    existing.context_length = model.get("context_length")
                else:
                    # New model from API - assume chat capability
                    capabilities = [ModelCapability.CHAT, ModelCapability.COMPLETION]

                    # Detect vision models by name patterns
                    if any(
                        pattern in model_id.lower()
                        for pattern in ["vl", "vision", "internvl", "gemma-3"]
                    ):
                        capabilities.append(ModelCapability.VISION)

                    # Detect embedding models
                    if any(pattern in model_id.lower() for pattern in ["e5", "embed"]):
                        capabilities = [ModelCapability.EMBEDDINGS]

                    self.model_registry.models[model_id] = ModelInfo(
                        id=model_id,
                        name=model_id,
                        capabilities=capabilities,
                        context_length=model.get("context_length"),
                    )

            logger.info(f"Loaded {len(self.model_registry.models)} models")

        except Exception as e:
            logger.warning(f"Failed to fetch models from API: {e}")
            # Keep known models even if API fails

        self.model_registry.last_updated = now

    async def update_models_from_docs(self, doc_content: str) -> list[str]:
        """Parse documentation content to discover additional models.

        This method can be called with documentation HTML/text to extract
        model information that may not be available via the /models endpoint.

        Args:
            doc_content: Documentation content (HTML or text).

        Returns:
            List of newly discovered model IDs.
        """
        new_models = []

        # Pattern to find model names in documentation
        # Look for patterns like "model-name-version" in various contexts
        model_patterns = [
            r"(?:model[\"']?\s*:\s*[\"']?)([a-z0-9][-a-z0-9._]+)(?:[\"']?)",
            r"(?:available models?[:\s]+)([a-z0-9][-a-z0-9._,\s]+)",
            r"(?:flux[.\-]?1[.\-]?schnell)",
            r"(?:whisper[-\s]?large[-\s]?v\d)",
            r"(?:e5[-\s]?mistral)",
            r"(?:multilingual[-\s]?e5)",
            r"(?:qwen[-\s]?image[-\s]?edit)",
        ]

        found_models = set()
        for pattern in model_patterns:
            matches = re.findall(pattern, doc_content.lower())
            found_models.update(matches)

        # Process found models
        for model_name in found_models:
            model_id = model_name.strip().replace(" ", "-")
            if model_id and model_id not in self.model_registry.models:
                # Infer capabilities from name
                capabilities = []
                name_lower = model_id.lower()

                if "whisper" in name_lower:
                    capabilities = [
                        ModelCapability.AUDIO_TRANSCRIPTION,
                        ModelCapability.AUDIO_TRANSLATION,
                    ]
                elif "flux" in name_lower:
                    capabilities = [ModelCapability.IMAGE_GENERATION]
                elif "image-edit" in name_lower or "img2img" in name_lower:
                    capabilities = [ModelCapability.IMAGE_EDIT]
                elif "e5" in name_lower or "embed" in name_lower:
                    capabilities = [ModelCapability.EMBEDDINGS]
                elif "vl" in name_lower or "vision" in name_lower:
                    capabilities = [ModelCapability.CHAT, ModelCapability.VISION]
                else:
                    capabilities = [ModelCapability.CHAT, ModelCapability.COMPLETION]

                self.model_registry.models[model_id] = ModelInfo(
                    id=model_id,
                    name=model_id,
                    capabilities=capabilities,
                    description=f"Model discovered from documentation: {model_id}",
                )
                new_models.append(model_id)

        return new_models

    def list_models(
        self,
        capability: ModelCapability | None = None,
    ) -> list[ModelInfo]:
        """List available models.

        Args:
            capability: Filter by capability.

        Returns:
            List of models.
        """
        if capability:
            return self.model_registry.get_models_by_capability(capability)
        return list(self.model_registry.models.values())

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create chat completion.

        Args:
            model: Model ID to use.
            messages: List of message dicts with 'role' and 'content'.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            top_p: Nucleus sampling parameter.
            stream: Whether to stream response.
            **kwargs: Additional parameters.

        Returns:
            Completion response.
        """
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or settings.default_max_tokens,
            "stream": stream,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p

        payload.update(kwargs)

        return await self._request("POST", "/chat/completions", json=payload)

    async def completion(
        self,
        model: str,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create text completion.

        Args:
            model: Model ID to use.
            prompt: Text prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            **kwargs: Additional parameters.

        Returns:
            Completion response.
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens or settings.default_max_tokens,
        }

        if temperature is not None:
            payload["temperature"] = temperature

        payload.update(kwargs)

        return await self._request("POST", "/completions", json=payload)

    async def embeddings(
        self,
        model: str,
        input_text: str | list[str],
        encoding_format: str = "float",
    ) -> dict[str, Any]:
        """Create embeddings.

        Args:
            model: Embedding model ID.
            input_text: Text or list of texts to embed.
            encoding_format: Output format ('float' or 'base64').

        Returns:
            Embeddings response.
        """
        payload = {
            "model": model,
            "input": input_text,
            "encoding_format": encoding_format,
        }

        return await self._request("POST", "/embeddings", json=payload)

    async def image_generation(
        self,
        prompt: str,
        model: str = "flux",
        n: int = 1,
        size: str = "1024x1024",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate images from text.

        Args:
            prompt: Text description of image to generate.
            model: Image generation model.
            n: Number of images to generate.
            size: Image size (e.g., '1024x1024').
            **kwargs: Additional parameters.

        Returns:
            Image generation response with base64 encoded images.
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": size,
        }
        payload.update(kwargs)

        return await self._request("POST", "/images/generations", json=payload)

    async def image_edit(
        self,
        image: str | bytes,
        prompt: str,
        model: str = "qwen-image-edit",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Edit image with text prompt.

        Args:
            image: Base64 encoded image, raw bytes, or file path.
            prompt: Edit instructions.
            model: Image edit model.
            **kwargs: Additional parameters.

        Returns:
            Image edit response.
        """
        # Handle different input types
        if isinstance(image, str):
            if Path(image).exists():
                # File path
                with open(image, "rb") as f:
                    image_bytes = f.read()
                filename = Path(image).name
            else:
                # Assume base64 encoded
                image_bytes = base64.b64decode(image)
                filename = "image.png"
        else:
            # Raw bytes
            image_bytes = image
            filename = "image.png"

        # Detect MIME type from file content
        mime_type = "image/png"  # default
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            mime_type = "image/png"
        elif image_bytes[:2] == b"\xff\xd8":
            mime_type = "image/jpeg"
        elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            mime_type = "image/webp"
        elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
            mime_type = "image/gif"

        # Use multipart form with inference-service header
        # Create a fresh client without Content-Type header for multipart
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(settings.request_timeout),
        ) as client:
            response = await client.post(
                "/edit-image/",
                headers={
                    "inference-service": "image-edit",
                },
                files={
                    "image": (filename, image_bytes, mime_type),
                },
                data={
                    "prompt": prompt,
                    "model": model,
                    **kwargs,
                },
            )
        response.raise_for_status()
        self.rate_limits.update_from_headers(dict(response.headers))

        # Response is raw image bytes, encode as base64
        image_b64 = base64.b64encode(response.content).decode("utf-8")
        return {
            "data": [{"b64_json": image_b64}],
            "created": None,
        }

    async def audio_transcription(
        self,
        audio_file: str | bytes,
        model: str = "whisper-large-v2",
        language: str | None = None,
        response_format: str = "text",
    ) -> dict[str, Any]:
        """Transcribe audio to text.

        Args:
            audio_file: Path to audio file or bytes.
            model: Transcription model.
            language: Language code (optional).
            response_format: Output format ('text', 'srt', 'vtt').

        Returns:
            Transcription response.
        """
        # Read file if path provided
        if isinstance(audio_file, str):
            with open(audio_file, "rb") as f:
                audio_data = f.read()
            filename = Path(audio_file).name
        else:
            audio_data = audio_file
            filename = "audio.wav"

        files = {"file": (filename, audio_data)}
        data = {
            "model": model,
            "response_format": response_format,
        }
        if language:
            data["language"] = language

        # Use audio endpoint (different base URL)
        response = await self.audio_http_client.post(
            "/audio/transcriptions",
            files=files,
            data=data,
        )
        response.raise_for_status()
        self.rate_limits.update_from_headers(dict(response.headers))
        return response.json() if response_format != "text" else {"text": response.text}

    async def audio_translation(
        self,
        audio_file: str | bytes,
        model: str = "whisper-large-v2",
        response_format: str = "text",
    ) -> dict[str, Any]:
        """Translate audio to English text.

        Args:
            audio_file: Path to audio file or bytes.
            model: Translation model.
            response_format: Output format ('text', 'srt', 'vtt').

        Returns:
            Translation response.
        """
        if isinstance(audio_file, str):
            with open(audio_file, "rb") as f:
                audio_data = f.read()
            filename = Path(audio_file).name
        else:
            audio_data = audio_file
            filename = "audio.wav"

        files = {"file": (filename, audio_data)}
        data = {
            "model": model,
            "response_format": response_format,
        }

        # Use audio endpoint (different base URL)
        response = await self.audio_http_client.post(
            "/audio/translations",
            files=files,
            data=data,
        )
        response.raise_for_status()
        self.rate_limits.update_from_headers(dict(response.headers))
        return response.json() if response_format != "text" else {"text": response.text}

    async def document_conversion(
        self,
        document: str | bytes,
        output_format: str = "markdown",
        extract_tables_as_images: bool = False,
        image_resolution_scale: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Convert document (PDF) to text format using Docling.

        Args:
            document: Path to document or bytes.
            output_format: Output format ('markdown', 'html', 'json', 'tokens').
            extract_tables_as_images: Whether to extract tables as images.
            image_resolution_scale: Image resolution scale (1-4).
            **kwargs: Additional parameters.

        Returns:
            Converted document response with markdown/html/json content.
        """
        if isinstance(document, str):
            with open(document, "rb") as f:
                doc_data = f.read()
            filename = Path(document).name
        else:
            doc_data = document
            filename = "document.pdf"

        # Use correct field name 'document' and endpoint '/documents/convert'
        # Create a fresh client without Content-Type header for multipart
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(settings.request_timeout),
        ) as client:
            response = await client.post(
                "/documents/convert",
                files={
                    "document": (filename, doc_data, "application/pdf"),
                },
                data={
                    "response_type": output_format,
                    "extract_tables_as_images": str(extract_tables_as_images).lower(),
                    "image_resolution_scale": str(image_resolution_scale),
                    **{k: str(v) for k, v in kwargs.items()},
                },
            )
        response.raise_for_status()
        self.rate_limits.update_from_headers(dict(response.headers))
        return response.json()

    async def chat_with_vision(
        self,
        model: str,
        messages: list[dict[str, Any]],
        images: list[str | bytes] | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Chat with vision model including images.

        Args:
            model: Vision-capable model ID.
            messages: List of messages.
            images: List of image paths or base64 data.
            max_tokens: Maximum tokens.
            **kwargs: Additional parameters.

        Returns:
            Chat completion response.
        """
        # Process images into content blocks - create copy to avoid mutating input
        processed_messages = [{**msg} for msg in messages]

        if images:
            content_parts = []

            # Add text content from last user message
            for msg in processed_messages:
                if msg.get("role") == "user":
                    content_parts.append({"type": "text", "text": msg.get("content", "")})
                    break

            # Add image content
            for img in images:
                if isinstance(img, str):
                    if Path(img).exists():
                        with open(img, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    else:
                        img_b64 = img  # Assume already base64
                else:
                    img_b64 = base64.b64encode(img).decode("utf-8")

                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })

            # Update last user message with multimodal content
            for msg in processed_messages:
                if msg.get("role") == "user":
                    msg["content"] = content_parts
                    break

        return await self.chat_completion(
            model=model,
            messages=processed_messages,
            max_tokens=max_tokens,
            **kwargs,
        )
