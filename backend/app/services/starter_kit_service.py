"""Builds the downloadable "starter kit" zip for a customer's cx session.

Phase 3 of the call-transcript-to-live-prototype feature (user's own words:
"provide options to download, code, access policy and scripts to deploy on
cx cloud" / "I also want cx to be able to download artifacts as a startup
kit"). This service generates NOTHING itself beyond static instructional
text and a plain zip container - the prototype content packaged inside is
exactly the same ``output_text`` an Azure-hosted agent already produced
during the workflow run (the same content served by ``GET /cx/{id}/app``),
and every policy figure quoted in ``ACCESS_POLICY.md`` is read directly
from ``Settings``/the provisioning service, never hardcoded.
"""
from __future__ import annotations

import io
import zipfile

from app.config.settings import Settings

__all__ = ["StarterKitError", "StarterKitService"]


class StarterKitError(Exception):
    """Raised when a starter kit cannot be built (e.g. no prototype yet)."""


class StarterKitService:
    """Packages a session's generated prototype plus deploy/access docs into a zip."""

    def build_zip(
        self,
        *,
        prototype_html: str,
        settings: Settings,
        dedicated_agent_count: int,
    ) -> bytes:
        if not prototype_html:
            raise StarterKitError(
                "The sample prototype has not been generated yet for this session."
            )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("prototype/index.html", prototype_html)
            archive.writestr("README.md", self._readme())
            archive.writestr(
                "ACCESS_POLICY.md",
                self._access_policy(settings=settings, dedicated_agent_count=dedicated_agent_count),
            )
        return buffer.getvalue()

    def _readme(self) -> str:
        return """# Genie Starter Kit

This package contains the sample prototype generated for your Genie session.

## Contents

- `prototype/index.html` - the generated sample application (a self-contained
  static HTML/CSS/JS page).
- `ACCESS_POLICY.md` - the access, rate-limit, and agent-lifecycle policy that
  applied to this deliverable.

## Hosting the prototype

`prototype/index.html` is a self-contained static file. You can host it on any
static web hosting service, for example Azure Static Web Apps:

    az staticwebapp create --name <your-app-name> --resource-group <your-rg> \\
      --source . --location <region> --branch main \\
      --app-location "prototype" --output-location "."

Or preview it locally:

    npx serve prototype

## Important

Any interactive "chat" or "reanalyze" controls embedded in the generated page
call back to your original Genie session URL and require your personal
cx-access link to still be valid - they will stop working once that link
expires or after your Genie session is explicitly closed. This kit is a
point-in-time snapshot of the generated prototype only; it does not include
Genie platform source code, credentials, or any other customer's data.
"""

    def _access_policy(self, *, settings: Settings, dedicated_agent_count: int) -> str:
        return f"""# Access Policy for this Deliverable

- Access token type: signed JWT, valid for {settings.cx_token_ttl_seconds} seconds
  from the time it was minted.
- Interactive actions (chat, reanalyze) are rate-limited to
  {settings.cx_reanalysis_rate_limit_per_hour} requests per hour, per session.
- Dedicated Azure AI Foundry agents provisioned for this session: {dedicated_agent_count}.
  These are torn down only when the session is explicitly closed by the Genie
  operator - never on an idle timeout.
- This starter kit contains only the generated deliverable content for this
  workflow run. It does not include Genie platform source code, credentials,
  connection strings, or any other customer's data.
"""
