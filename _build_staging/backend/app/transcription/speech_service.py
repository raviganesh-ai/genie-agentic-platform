"""Azure AI Speech transcription service selection.

Mirrors ``app.agents.gateway`` (``create_agent_gateway``):
``AzureSpeechToTextService`` is used whenever Azure AI Speech is
configured; ``LocalSpeechToTextService`` is a deterministic, non-network
stub for local development, used only when ``allow_local_agents`` is True.
Reuses ``allow_local_agents`` rather than introducing a parallel flag, so
this capability follows the exact same gating already established for
agent execution.
"""
from __future__ import annotations

import json
from typing import Protocol

from app.config.settings import Settings
from app.transcription.models import TranscriptionResult

__all__ = [
    "AzureSpeechToTextService",
    "LocalSpeechToTextService",
    "SpeechServiceUnavailableError",
    "SpeechToTextError",
    "SpeechToTextService",
    "create_speech_to_text_service",
]


class SpeechToTextError(RuntimeError):
    """Base error for speech-to-text transcription failures."""


class SpeechServiceUnavailableError(SpeechToTextError):
    """Raised when Azure AI Speech is not configured or cannot be reached (fail closed)."""


class SpeechToTextService(Protocol):
    """Transcribes one audio/video recording into text."""

    async def transcribe(
        self, *, audio_bytes: bytes, content_type: str, file_name: str
    ) -> TranscriptionResult: ...


class LocalSpeechToTextService:
    """Deterministic, non-network transcription stub for local dev/tests only.

    Mirrors ``LocalAgentGateway``: proves the upload -> transcription ->
    workflow-input contract works without real Azure AI Speech credentials.
    Performs no real speech recognition.
    """

    async def transcribe(
        self, *, audio_bytes: bytes, content_type: str, file_name: str
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text=(
                f"[local-speech-stub] transcribed {len(audio_bytes)} bytes from "
                f"'{file_name}' ({content_type}); no real speech recognition performed."
            )
        )


class AzureSpeechToTextService:
    """Real Azure AI Speech transcription via the Fast Transcription REST API.

    Uses Microsoft Entra ID (``DefaultAzureCredential``) rather than a
    subscription key, per the Security Requirements in
    ``.github/copilot-instructions.md``. The calling managed identity must
    be granted a role that includes Cognitive Services Speech data actions
    on the target Speech/AIServices resource (e.g. "Cognitive Services
    Speech User" or "Cognitive Services User").

    ASSUMPTION (isolated behind this class so it can be corrected without
    touching call sites): the Fast Transcription REST API
    (``/speechtotext/transcriptions:transcribe``) accepts an Entra ID
    bearer token the same way other Cognitive Services REST APIs do. If a
    given Speech resource rejects AAD auth, swap the token acquisition
    below for a Key Vault-sourced subscription key without changing any
    caller.
    """

    _API_VERSION = "2024-11-15"

    def __init__(self, *, endpoint: str) -> None:
        if not endpoint.strip():
            raise SpeechServiceUnavailableError("Azure AI Speech endpoint must not be blank.")
        self._endpoint = endpoint.rstrip("/")

    async def transcribe(
        self, *, audio_bytes: bytes, content_type: str, file_name: str
    ) -> TranscriptionResult:
        try:
            import httpx
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise SpeechServiceUnavailableError(
                "httpx / azure-identity are not installed; cannot reach Azure AI Speech."
            ) from exc

        try:
            token = DefaultAzureCredential().get_token(
                "https://cognitiveservices.azure.com/.default"
            )
        except Exception as exc:
            raise SpeechServiceUnavailableError(
                f"Failed to acquire an Azure AD token for Azure AI Speech: {exc}"
            ) from exc

        url = (
            f"{self._endpoint}/speechtotext/transcriptions:transcribe"
            f"?api-version={self._API_VERSION}"
        )
        definition = json.dumps({"locales": ["en-US"]})
        files = {
            "audio": (file_name, audio_bytes, content_type),
            "definition": (None, definition, "application/json"),
        }
        headers = {"Authorization": f"Bearer {token.token}"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, files=files)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise SpeechToTextError(f"Azure AI Speech transcription request failed: {exc}") from exc

        text = " ".join(
            phrase.get("text", "") for phrase in (payload.get("combinedPhrases") or [])
        ).strip()
        if not text:
            # Fall back to the per-phrase shape in case a given API version
            # omits "combinedPhrases".
            text = " ".join(
                phrase.get("text", "") for phrase in (payload.get("phrases") or [])
            ).strip()

        duration_ms = payload.get("durationMilliseconds")
        return TranscriptionResult(
            text=text,
            duration_seconds=(duration_ms / 1000.0) if duration_ms else None,
        )


def create_speech_to_text_service(settings: Settings) -> SpeechToTextService:
    """Select the transcription backend for the current configuration.

    Mirrors ``create_agent_gateway``: ``LocalSpeechToTextService`` when
    ``allow_local_agents`` is True, otherwise requires real Azure AI Speech
    configuration (fails closed).
    """

    def _build_azure_service() -> SpeechToTextService:
        if not settings.azure_speech_endpoint:
            raise SpeechServiceUnavailableError(
                "azure_speech_endpoint must be configured to use AzureSpeechToTextService."
            )
        return AzureSpeechToTextService(endpoint=settings.azure_speech_endpoint)

    if settings.allow_local_agents:
        return LocalSpeechToTextService()

    if settings.azure_speech_endpoint:
        return _build_azure_service()

    raise SpeechServiceUnavailableError(
        "No usable speech-to-text backend: allow_local_agents is False and "
        "Azure AI Speech is not configured."
    )
