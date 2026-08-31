# MISE Key-Discovery Telemetry Verification

## Deployment under review

- Service: Genie Agentic Experience Center
- Environment: production (`genie-dev-rg` / `genie-backend`)
- API application ID: `de371e21-f85e-4f7c-942f-ff81c9c7c907`
- Gateway: .NET 8 with `Microsoft.Identity.ServiceEssentials.AspNetCore` `2.5.3`
- Request path: Azure Container Apps ingress on `8080` -> MISE gateway -> FastAPI on replica-local `8000`
- Deployment revision: pending successful CI/CD rollout
- Authenticated traffic window (UTC): pending successful CI/CD rollout
- Correlation IDs and HTTP status codes: pending successful CI/CD rollout
- MCAPS SFI confirmation: pending

## Evidence collection

After the CI/CD deployment succeeds, record the ready revision and run:

```powershell
./scripts/generate_gateway_traffic.ps1 `
  -ApiBaseUrl https://genie-backend.gentleisland-5683ce78.eastus2.azurecontainerapps.io `
  -ClientId de371e21-f85e-4f7c-942f-ff81c9c7c907 `
  -RequestCount 5
```

The script acquires a short-lived access token with the current Azure CLI identity, keeps the token out of output and disk, and prints only timestamps, correlation IDs, and response statuses. Attach the resulting non-secret evidence and the ready Container App revision to the request below.

## Request to MCAPS SFI Compliance

**Subject:** Verify MISE key-discovery telemetry for Genie authentication gateway

Please verify that MISE key-discovery telemetry was received for the Genie production authentication gateway during the UTC window recorded above.

The gateway uses `Microsoft.Identity.ServiceEssentials.AspNetCore` version `2.5.3` and validates access tokens for API application ID `de371e21-f85e-4f7c-942f-ff81c9c7c907`. It runs in every `genie-backend` Container App replica and is the only target of external ingress. The attached correlation IDs identify authenticated requests that traversed MISE and were then forwarded to the private FastAPI container for defense-in-depth validation.

Please confirm:

1. Key-discovery telemetry was emitted by the listed deployment revision during the recorded window.
2. The telemetry is associated with the stated API application ID and MISE version.
3. Every active replica reported the expected signal.
4. No configuration or traffic change is required for ongoing SFI compliance.

Do not include access tokens, PATs, or other credentials in the response or attached evidence.