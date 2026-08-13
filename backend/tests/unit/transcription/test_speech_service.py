"""Unit tests for app.transcription.speech_service."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.transcription.speech_service import (
    AzureSpeechToTextService,
    LocalSpeechToTextService,
    SpeechServiceUnavailableError,
    create_speech_to_text_service,
)


async def test_local_speech_to_text_service_transcribes_deterministically():
    service = LocalSpeechToTextService()
    result = await service.transcribe(
        audio_bytes=b"abc", content_type="audio/wav", file_name="call.wav"
    )
    assert "3 bytes" in result.text
    assert "call.wav" in result.text
    assert "audio/wav" in result.text


def test_azure_speech_service_rejects_blank_endpoint():
    with pytest.raises(SpeechServiceUnavailableError):
        AzureSpeechToTextService(endpoint="   ")


def test_create_service_uses_local_stub_by_default():
    settings = Settings(allow_local_agents=True)
    service = create_speech_to_text_service(settings)
    assert isinstance(service, LocalSpeechToTextService)


def test_create_service_uses_azure_when_local_agents_disallowed_and_configured():
    settings = Settings(
        allow_local_agents=False,
        azure_speech_endpoint="https://genie-speech.example.cognitiveservices.azure.com",
    )
    service = create_speech_to_text_service(settings)
    assert isinstance(service, AzureSpeechToTextService)


def test_create_service_fails_closed_when_no_backend_usable():
    settings = Settings(allow_local_agents=False, azure_speech_endpoint=None)
    with pytest.raises(SpeechServiceUnavailableError):
        create_speech_to_text_service(settings)
