"""
Tests for Realtime Provider Factory.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (7.1.3)
- voice-ai-service/realtime/providers/factory.py
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRealtimeProviderFactory:
    """Testes para o Provider Factory."""
    
    @pytest.fixture
    def mock_credentials(self):
        """Credenciais mock."""
        return {
            "api_key": "test-api-key",
            "model": "gpt-4o-realtime-preview",
        }
    
    @pytest.fixture
    def mock_config(self):
        """Configuração mock."""
        from realtime.providers.base import RealtimeConfig
        return RealtimeConfig(
            domain_uuid="test-domain",
            provider_name="openai",
            system_prompt="Test prompt",
            voice="alloy"
        )
    
    def test_factory_has_all_providers(self):
        """Verifica que todos os providers estão registrados."""
        from realtime.providers.factory import RealtimeProviderFactory
        
        expected_providers = [
            "openai",
            "openai_realtime",
            "elevenlabs",
            "elevenlabs_conversational",
            "gemini",
            "gemini_live",
            "custom",
            "custom_pipeline",
        ]
        
        for provider_name in expected_providers:
            assert provider_name in RealtimeProviderFactory._providers
    
    def test_factory_get_available_providers(self):
        """Testa listagem de providers disponíveis."""
        from realtime.providers.factory import RealtimeProviderFactory
        
        providers = RealtimeProviderFactory.get_available_providers()
        
        assert "openai" in providers
        assert "elevenlabs" in providers
        assert "gemini" in providers
        assert "custom" in providers
    
    @pytest.mark.asyncio
    async def test_factory_create_openai_provider(self, mock_credentials, mock_config):
        """Testa criação de provider OpenAI."""
        from realtime.providers.factory import RealtimeProviderFactory
        from realtime.providers.openai_realtime import OpenAIRealtimeProvider
        
        with patch.object(
            RealtimeProviderFactory,
            '_get_credentials',
            return_value=mock_credentials
        ):
            provider = await RealtimeProviderFactory.create(
                provider_name="openai",
                domain_uuid="test-domain",
                config=mock_config
            )
            
            assert isinstance(provider, OpenAIRealtimeProvider)
    
    @pytest.mark.asyncio
    async def test_factory_create_elevenlabs_provider(self, mock_config):
        """Testa criação de provider ElevenLabs."""
        from realtime.providers.factory import RealtimeProviderFactory
        from realtime.providers.elevenlabs_conv import ElevenLabsConversationalProvider
        
        credentials = {
            "api_key": "test-api-key",
            "agent_id": "test-agent-id",
        }
        
        with patch.object(
            RealtimeProviderFactory,
            '_get_credentials',
            return_value=credentials
        ):
            provider = await RealtimeProviderFactory.create(
                provider_name="elevenlabs",
                domain_uuid="test-domain",
                config=mock_config
            )
            
            assert isinstance(provider, ElevenLabsConversationalProvider)
    
    @pytest.mark.asyncio
    async def test_factory_create_gemini_provider(self, mock_config):
        """Testa criação de provider Gemini."""
        from realtime.providers.factory import RealtimeProviderFactory
        from realtime.providers.gemini_live import GeminiLiveProvider
        
        credentials = {
            "api_key": "test-api-key",
            "model": "gemini-2.0-flash-exp",
        }
        
        with patch.object(
            RealtimeProviderFactory,
            '_get_credentials',
            return_value=credentials
        ):
            provider = await RealtimeProviderFactory.create(
                provider_name="gemini",
                domain_uuid="test-domain",
                config=mock_config
            )
            
            assert isinstance(provider, GeminiLiveProvider)
    
    @pytest.mark.asyncio
    async def test_factory_create_custom_provider(self, mock_config):
        """Testa criação de provider Custom Pipeline."""
        from realtime.providers.factory import RealtimeProviderFactory
        from realtime.providers.custom_pipeline import CustomPipelineProvider
        
        credentials = {
            "deepgram_key": "test-deepgram-key",
            "groq_key": "test-groq-key",
        }
        
        with patch.object(
            RealtimeProviderFactory,
            '_get_credentials',
            return_value=credentials
        ):
            provider = await RealtimeProviderFactory.create(
                provider_name="custom",
                domain_uuid="test-domain",
                config=mock_config
            )
            
            assert isinstance(provider, CustomPipelineProvider)
    
    @pytest.mark.asyncio
    async def test_factory_unknown_provider(self, mock_config):
        """Testa erro com provider desconhecido."""
        from realtime.providers.factory import RealtimeProviderFactory
        
        with pytest.raises(ValueError) as exc_info:
            await RealtimeProviderFactory.create(
                provider_name="unknown_provider",
                domain_uuid="test-domain",
                config=mock_config
            )
        
        assert "Unknown provider" in str(exc_info.value)


class TestBaseRealtimeProvider:
    """Testes para a interface base de providers."""
    
    def test_provider_event_types(self):
        """Verifica tipos de eventos disponíveis."""
        from realtime.providers.base import ProviderEventType
        
        assert hasattr(ProviderEventType, 'AUDIO_DELTA')
        assert hasattr(ProviderEventType, 'AUDIO_DONE')
        assert hasattr(ProviderEventType, 'TRANSCRIPT_DELTA')
        assert hasattr(ProviderEventType, 'TRANSCRIPT_DONE')
        assert hasattr(ProviderEventType, 'SPEECH_STARTED')
        assert hasattr(ProviderEventType, 'SPEECH_STOPPED')
        assert hasattr(ProviderEventType, 'FUNCTION_CALL')
        assert hasattr(ProviderEventType, 'ERROR')
    
    def test_provider_event_creation(self):
        """Testa criação de ProviderEvent."""
        from realtime.providers.base import ProviderEvent, ProviderEventType
        
        event = ProviderEvent(
            type=ProviderEventType.AUDIO_DELTA,
            data={"audio": b"test"}
        )
        
        assert event.type == ProviderEventType.AUDIO_DELTA
        assert event.data["audio"] == b"test"
    
    def test_realtime_config(self):
        """Testa RealtimeConfig."""
        from realtime.providers.base import RealtimeConfig
        
        config = RealtimeConfig(
            domain_uuid="test-domain",
            provider_name="openai",
            system_prompt="You are a helpful assistant.",
            first_message="Hello!",
            voice="alloy",
            vad_threshold=0.5,
            silence_timeout_ms=1000
        )
        
        assert config.domain_uuid == "test-domain"
        assert config.provider_name == "openai"
        assert config.system_prompt == "You are a helpful assistant."
        assert config.voice == "alloy"
        assert config.vad_threshold == 0.5
