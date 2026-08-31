"""Tests for SAIA client."""

import pytest

from saia_mcp.client import ModelCapability, ModelInfo, ModelRegistry, SAIAClient


class TestModelRegistry:
    """Tests for ModelRegistry."""

    def test_known_models_loaded(self):
        """Test that known models are in the registry."""
        registry = ModelRegistry()
        # Known models should be in the default factory
        assert "flux" in registry.KNOWN_MODELS  # Image generation
        assert "whisper-large-v2" in registry.KNOWN_MODELS  # Audio
        assert "e5-mistral-7b-instruct" in registry.KNOWN_MODELS  # Embeddings
        assert "llama-3.3-70b-instruct" in registry.KNOWN_MODELS  # Chat
        assert "qwen2.5-coder-32b-instruct" in registry.KNOWN_MODELS  # Code
        assert "deepseek-r1" in registry.KNOWN_MODELS  # Reasoning

    def test_get_models_by_capability(self):
        """Test filtering models by capability."""
        registry = ModelRegistry()

        # Add some test models
        registry.models["test-chat"] = ModelInfo(
            id="test-chat",
            name="Test Chat",
            capabilities=[ModelCapability.CHAT],
        )
        registry.models["test-embed"] = ModelInfo(
            id="test-embed",
            name="Test Embed",
            capabilities=[ModelCapability.EMBEDDINGS],
        )

        chat_models = registry.get_models_by_capability(ModelCapability.CHAT)
        assert len(chat_models) == 1
        assert chat_models[0].id == "test-chat"

        embed_models = registry.get_models_by_capability(ModelCapability.EMBEDDINGS)
        assert len(embed_models) == 1
        assert embed_models[0].id == "test-embed"


class TestSAIAClient:
    """Tests for SAIAClient."""

    def test_client_initialization(self):
        """Test client initializes correctly."""
        client = SAIAClient(api_key="test-key", base_url="https://test.example.com/v1")
        assert client.api_key == "test-key"
        assert client.base_url == "https://test.example.com/v1"

    def test_client_default_base_url(self):
        """Test client uses default base URL."""
        client = SAIAClient(api_key="test-key")
        assert "academiccloud.de" in client.base_url

    def test_list_models_empty(self):
        """Test listing models when registry is empty."""
        client = SAIAClient(api_key="test-key")
        models = client.list_models()
        assert isinstance(models, list)

    def test_list_models_with_filter(self):
        """Test listing models with capability filter."""
        client = SAIAClient(api_key="test-key")

        # Add test models
        client.model_registry.models["chat-model"] = ModelInfo(
            id="chat-model",
            name="Chat Model",
            capabilities=[ModelCapability.CHAT],
        )
        client.model_registry.models["embed-model"] = ModelInfo(
            id="embed-model",
            name="Embed Model",
            capabilities=[ModelCapability.EMBEDDINGS],
        )

        chat_models = client.list_models(ModelCapability.CHAT)
        assert len(chat_models) == 1

        all_models = client.list_models()
        assert len(all_models) == 2

    @pytest.mark.asyncio
    async def test_update_models_from_docs(self):
        """Test parsing documentation for models."""
        client = SAIAClient(api_key="test-key")

        # Test doc content with model references
        doc_content = """
        Available models include:
        - flux.1-schnell for image generation
        - whisper-large-v2 for transcription
        - some-new-model for testing
        """

        new_models = await client.update_models_from_docs(doc_content)
        # Should find models mentioned in docs
        assert isinstance(new_models, list)


class TestModelCapability:
    """Tests for ModelCapability enum."""

    def test_capability_values(self):
        """Test capability enum values."""
        assert ModelCapability.CHAT.value == "chat"
        assert ModelCapability.EMBEDDINGS.value == "embeddings"
        assert ModelCapability.VISION.value == "vision"
        assert ModelCapability.IMAGE_GENERATION.value == "image_generation"
        assert ModelCapability.AUDIO_TRANSCRIPTION.value == "audio_transcription"
