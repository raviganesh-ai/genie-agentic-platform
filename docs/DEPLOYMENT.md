# Deploying Genie

Genie has two supported deployment paths:

1. **Quickstart (recommended for a first deployment)** — one script, `scripts/deploy_quickstart.ps1`, that provisions a complete, isolated Genie evaluation environment in any Azure subscription you own. See [Quickstart: deploy to your own Azure subscription](../README.md#quickstart-deploy-to-your-own-azure-subscription) in the main README.
2. **Manual / advanced** — the same steps the quickstart automates, run individually. Use this path if you want to customize a stage, reuse existing Azure resources, or understand exactly what the quickstart does under the hood. This is also the path Genie's own continuous deployment pipeline follows (see [Continuous deployment](#continuous-deployment-github-actions) below).

Both paths provision the same architecture (see [Architecture](../README.md#architecture) in the main README): Bicep infrastructure-as-code creates foundational Azure resources and a dedicated APIM subnet, a public Standard v2 APIM gateway with outbound VNet integration is the only Internet-facing endpoint, a FastAPI Container App is reached only through its environment private endpoint, and a static React frontend is deployed to Azure Static Web Apps. Generated prototypes (Deploy & Launch) are separate and get their own APIM service and private Container Apps environment per mission. Deployment is split into readiness validation → infrastructure provisioning → agent provisioning → gateway/private-network cutover → application deployment, matching the repo's fail-closed philosophy: nothing proceeds until the previous step is verified. These instructions reproduce the **evaluation environment**; they do not supersede the production-readiness work required by the [Purpose and use boundary](../README.md#purpose-and-use-boundary).

> **`infra/main.bicep` alone does not create API Management.** APIM is a separate template, `infra/platform-private-gateway.bicep`, applied by `scripts/deploy_platform_gateway.ps1` as its own later stage - it can only be wired up once the backend Container App that `main.bicep` creates already exists, and the private-network cutover it performs is a deliberately imperative, verified sequence (prove APIM reaches the backend → prepare private DNS → disable public access → create the private endpoint → re-verify) rather than a single declarative template. `scripts/deploy_quickstart.ps1` runs both of these automatically, in order, as part of its one command; if you deploy `infra/main.bicep` by itself (for example via `az deployment sub create` directly, or `scripts/deploy_infra.ps1`), you will correctly see no APIM at all afterward - that is expected, not a bug. See step 6 in [the manual deployment steps](#6-deploying-application-code-backend--frontend) below for the equivalent manual command.

### Removing a previous deployment before a fresh retry

If an earlier deployment attempt for the same `(ResourcePrefix, EnvironmentName)` pair left a partial or broken resource group behind, remove it first:

```powershell
./scripts/remove_existing_deployment.ps1 -SubscriptionId <subscription-id> -EnvironmentName dev -Location eastus2
```

Deliberately narrow in scope: it only ever inspects and removes the single resource group `infra/main.bicep` creates (`<prefix>-<environment>-rg`), which also holds the APIM gateway `platform-private-gateway.bicep` adds into that same resource group. It never touches per-mission prototype resource groups (`genie-proto-*` - those have their own TTL/cleanup reconciler) or the one-time deployment identity from step 1 below. Nothing is deleted without an explicit confirmation - you must type the resource group's exact name back, or re-run the script for a non-interactive build runner, with `-Yes`.

**Key Vault purge protection caveat (Azure behavior, not a limitation of this script):** `infra/modules/key-vault.bicep` enables purge protection deliberately. Deleting the resource group only *soft*-deletes its Key Vault, and Azure then refuses to let **any** vault reuse that exact name for 90 days - no CLI flag can override this once purge protection is on. Because the vault name is derived deterministically from `(subscription, EnvironmentName, Location)`, redeploying with the **same** `-EnvironmentName` after a delete will hit that same name again. The script detects this up front and tells you plainly; the two real workarounds are to pick a **different** `-EnvironmentName` for an immediately clean redeploy, or wait out the retention window.

`scripts/deploy_quickstart.ps1` runs this check automatically (always with the same confirmation, or pass its own `-RemovePreviousDeployment` switch to skip the prompt) before provisioning anything.

---

## Manual deployment

### Prerequisites

- Azure CLI (`az`) installed and logged in (`az login`) against the target subscription and tenant — **your own**, never anyone else's.
- Contributor-equivalent (or the custom role generated below) rights on the target subscription.
- PowerShell (scripts are `.ps1`; can be run via `pwsh` on non-Windows too).

### 1. Create a least-privilege deployment identity (optional but recommended)

```powershell
python scripts/generate_deployment_role_definition.py   # renders a custom RBAC role from config/deployment/resource_providers.yaml
./scripts/create_deployment_identity.ps1                # creates/assigns the "Genie Infrastructure Deployer" role
```

This role only grants `<namespace>/*` actions for the 12 resource-provider namespaces Genie actually needs (never `Owner`/`Contributor`). If your tenant blocks service-principal secret/certificate creation (common in managed/enterprise tenants), the script falls back to assigning the role directly to your signed-in user account.

### 2. Validate deployment readiness

```powershell
python scripts/validate_deployment_readiness.py
```

Confirms every required Azure resource-provider namespace (`Microsoft.Resources`, `Authorization`, `ManagedIdentity`, `KeyVault`, `Storage`, `CognitiveServices`, `Search`, `DocumentDB`, `App`, `Web`, `OperationalInsights`, `Insights`, plus `Marketplace`/`MarketplaceOrdering`/`SaaS` if you plan to deploy partner models) is `Registered`. Registers via:

```powershell
az provider register --namespace <namespace>
```

Re-run the validator until every provider shows `Registered` (registration is asynchronous and can take several minutes) before proceeding.

### 3. Provision infrastructure

```powershell
./scripts/deploy_infra.ps1 -EnvironmentName dev -Location eastus2
# Optional: -AiSearchLocation eastus   (if AI Search lacks capacity in the primary region)
```

This gates on step 2 passing, then runs:

```powershell
az deployment sub create `
  --location <location> `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.json environmentName=<env> location=<location>
```

`main.bicep` is **subscription-scoped** and assumes the target subscription is empty — it creates its own resource group (`<resourcePrefix>-<environmentName>-rg`, default prefix `genie`) and every foundational resource:

| Module | Resource |
|---|---|
| `managed-identity.bicep` | User-assigned managed identity (shared by every resource below via least-privilege RBAC) |
| `key-vault.bicep` | Azure Key Vault |
| `storage-account.bicep` | Azure Storage Account |
| `ai-search.bicep` | Azure AI Search (Enterprise Knowledge Memory) |
| `cosmos-db.bicep` | Cosmos DB (Shared/Personal Memory, sessions) |
| `ai-foundry.bicep` | Azure AI Foundry account + project, plus any `foundryModelDeployments` you pass |
| `virtual-network.bicep` | VNet with dedicated Container Apps, private endpoint, and delegated APIM integration subnets |
| `container-apps-environment.bicep` | Container Apps environment (hosts the backend) |
| `container-registry.bicep` | Azure Container Registry (only when `deployContainerRegistryAndBackendApp` is `true`, the default) |
| `backend-container-app.bicep` | The backend Container App itself, bootstrapped with a public placeholder image (only when `deployContainerRegistryAndBackendApp` is `true`) |
| `static-web-app.bicep` | Azure Static Web App (hosts the frontend) |
| `log-analytics.bicep` + `app-insights.bicep` | Observability |

Every resource is granted only the specific RBAC role it needs on the shared managed identity (Key Vault Secrets User, Storage Blob Data Contributor, Search Index Data Contributor, Cognitive Services User, Cosmos DB Built-in Data Contributor, AcrPull) — never a broad Owner/Contributor grant.

After the backend Container App exists, `infra/platform-private-gateway.bicep` adds the Standard v2 APIM service and anonymous proxy API, exact-origin policy, private endpoint, and `privatelink.<region>.azurecontainerapps.io` DNS integration. `scripts/deploy_platform_gateway.ps1` controls the safe cutover rather than having foundational provisioning disable access before a gateway can be verified.

Capture the outputs (`aiFoundryEndpoint`, `aiSearchEndpoint`, `keyVaultUri`, `storageAccountName`, `cosmosDbEndpoint`, `managedIdentityPrincipalId`, `containerAppsEnvironmentId`, `containerRegistryName`, `containerRegistryLoginServer`, `backendContainerAppName`, `staticWebAppDefaultHostname`, `applicationInsightsConnectionString`) via:

```powershell
az deployment sub show --name <deployment-name> --query properties.outputs
```

### 4. Deploy an LLM model

Deploy at least one model to the AI Foundry account (Cognitive Services deployment) — either by passing `foundryModelDeployments` to `infra/main.bicep` in step 3, or afterward. For models sold directly by Azure (e.g. `gpt-5.1`):

```powershell
az cognitiveservices account deployment create `
  --name <foundry-account-name> --resource-group <rg> `
  --deployment-name <deployment-name> --model-name <model> --model-version <version> `
  --model-format OpenAI --sku-name GlobalStandard --sku-capacity 10
```

For Azure Marketplace / partner models (e.g. Anthropic Claude), the CLI has no flag for `ModelProviderData` — use a direct REST call instead:

```powershell
az rest --method put `
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/deployments/<name>?api-version=2026-05-15-preview" `
  --body '{"properties":{"model":{"format":"...","name":"...","version":"..."},"modelProviderData":{"industry":"Technology","organizationName":"<org>","countryCode":"US"}},"sku":{"name":"GlobalStandard","capacity":1}}'
```

> Requires the target Marketplace SKU to actually have non-zero quota on the subscription — request a quota increase first if `InsufficientQuota` is returned.

Set `Settings.default_llm` / `GENIE_DEFAULT_LLM` (and any per-agent `model_deployment_ref` overrides in `config/agents/registry.yaml`) to match the deployment name you chose.

### 5. Provisioning Foundry agents

Genie agents are **real Foundry agent resources**, provisioned once per environment — never created ad hoc by the running application. Every enabled agent in `config/agents/registry.yaml` already has a fixed, chosen `foundry_agent_id` (the Foundry `agent_name`) committed to the repo — there is no name to invent or paste back for a normal deployment.

```powershell
# Requires the caller to hold "Cognitive Services User" (or equivalent) at the
# Foundry PROJECT scope (not just the account scope) for data-plane writes.
python scripts/provision_foundry_agents.py --endpoint <foundry-endpoint> --project <foundry-project-name>
```

Uses the `azure-ai-projects` SDK (`AIProjectClient(endpoint=..., credential=DefaultAzureCredential())`) to create or update one Foundry agent per enabled entry in `config/agents/registry.yaml`.

Verify every agent resolves correctly:

```powershell
python scripts/validate_foundry_agents.py     # runs the fail-closed Foundry validators directly
python scripts/sync_foundry_agents.py         # confirms each enabled agent's foundry_agent_id exists (status: synchronized)
python scripts/export_foundry_inventory.py    # dumps the config-derived inventory as JSON
```

All four require `GENIE_AZURE_FOUNDRY_ENDPOINT` / `GENIE_AZURE_FOUNDRY_PROJECT_NAME` to be set and must be run from the repository root (so the relative `config/` path resolves correctly).

### 6. Deploying application code (backend + frontend)

**Backend (Azure Container Apps)**

1. Build and push the immutable FastAPI image to Azure Container Registry:

   ```powershell
   az acr build --registry <acr-name> --image genie-backend:<commit> `
     --file backend/Dockerfile <source>
   ```

   `<source>` can be a local directory (`.`) or a git URL (`https://github.com/<org>/<repo>.git#<branch>`, optionally with an embedded token for a private repo: `https://<token>@github.com/...`) — use whichever works reliably in your build environment.

2. Provision APIM, prove it reaches the current backend, prepare private DNS, disable Container Apps environment public access, create the private endpoint, and prove both the private gateway route and direct-route denial:

   ```powershell
   $gateway = ./scripts/deploy_platform_gateway.ps1 `
     -SubscriptionId <subscription-id> `
     -ResourceGroup <rg> `
     -ContainerAppName <backend-container-app-name> `
     -AllowedOrigin https://<static-web-app-host> `
     -PublisherEmail <publisher-email> `
     -PublisherName "Genie" | ConvertFrom-Json
   ```

   The first deployment can take tens of minutes while Standard v2 APIM is created. Public Container Apps access is not changed unless APIM is healthy and private DNS preparation succeeds. Azure requires environment public access to be disabled before private endpoint creation, so an endpoint failure leaves the backend closed rather than restoring public ingress. Repeated runs are idempotent and resume a closed-but-incomplete cutover by creating the missing private endpoint before probing APIM.

3. Atomically update the backend image, remove retired gateway containers and auth settings, configure CORS/probes, target FastAPI port `8000`, and verify the revision through APIM:

   ```powershell
   ./scripts/deploy_backend.ps1 `
     -SubscriptionId <subscription-id> `
     -ResourceGroup <rg> `
     -ContainerAppName <backend-container-app-name> `
     -BackendImage <acr-login-server>/genie-backend:<commit> `
     -AllowedOrigin https://<static-web-app-host> `
     -GatewayUrl $gateway.gatewayUrl `
     -MemoryStoreEndpoint https://<cosmos-account>.documents.azure.com/ `
     -GitHubMcpEndpoint https://api.githubcopilot.com/mcp/ `
     -GitHubMcpTokenSecretName github-mcp-token `
     -PrototypeApiGatewayPublisherEmail <publisher-email> `
     -PrototypeApiGatewayPublisherName "Genie" `
     -PrototypeMaxActivePerOwner 0 `
     -RevisionSuffix <unique-suffix>
   ```

   The script preserves the existing identity, environment, secrets, resources, and unrelated containers; removes `genie-auth-gateway`/`mise-sidecar` plus stale user-auth settings; and applies the image, CORS, probes, and ingress target in one ARM patch. It requires environment public access to remain `Disabled`, waits for the exact revision, and verifies readiness plus anonymous `GET /sessions` through APIM. Roll back by running the same script with the previous backend image tag and a new revision suffix. Set the referenced secret first: `az containerapp secret set --name <app> -g <rg> --secrets github-mcp-token=<your-own-github-pat>`.

   > If you're using a **user-assigned** managed identity, retain `AZURE_CLIENT_ID=<identity-client-id>` or `DefaultAzureCredential` cannot resolve which identity to use and the container will crash-loop.

4. Verify:

   ```powershell
   curl https://<apim-name>.azure-api.net/health/live
   curl https://<apim-name>.azure-api.net/health/ready
   curl -i https://<apim-name>.azure-api.net/sessions  # must return 200
   curl -i https://<container-app-fqdn>/health/ready   # must not return 2xx
   ```

**Frontend (Azure Static Web Apps)**

1. Set `VITE_GENIE_API_BASE_URL` to the APIM gateway URL for the build. Production CI receives this URL directly from the verified `prepare-gateway` job; no production endpoint is committed in an env file.
2. Build:

   ```powershell
   cd frontend
   npm ci
   npm run build      # -> dist/
   ```

3. Deploy:

   ```powershell
   $token = az staticwebapp secrets list --name <swa-name> --resource-group <rg> --query properties.apiKey -o tsv
   npx @azure/static-web-apps-cli deploy dist --deployment-token $token --env production
   ```

### 7. Content Understanding (optional)

Content Understanding (Discovery document/image evidence ingestion) requires completion and embedding model aliases on the AIServices resource. Configure them once per environment with externally supplied deployment/model names:

```powershell
./scripts/configure_content_understanding.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroup <resource-group> `
  -AccountName <ai-services-account> `
  -CompletionDeploymentName <completion-deployment> `
  -CompletionModelName <supported-completion-model> `
  -CompletionModelVersion <completion-model-version> `
  -EmbeddingDeploymentName <embedding-deployment> `
  -EmbeddingModelName <supported-embedding-model> `
  -EmbeddingModelVersion <embedding-model-version>
```

The script uses Microsoft Entra authentication, creates only missing model deployments, checks both model families against the live analyzer's `supportedModels`, PATCHes the analyzer aliases as resource defaults, and reads them back. The runtime managed identity requires **Cognitive Services User** on the AIServices account. Production startup independently reads the analyzer and defaults and refuses readiness when the analyzer is unavailable or any required alias is unmapped.

---

## Continuous deployment (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR to `master` (the repo's actual default branch - confirmed via `git branch -a`/`git remote show origin`/`gh repo view --json defaultBranchRef`). On a real push to `master`, once the `backend` and `frontend` CI jobs pass, two deploy jobs run the exact same steps documented above, automatically:

- **`prepare-gateway`** — logs into Azure via OIDC federated credential (no client secret), idempotently provisions Standard v2 APIM and its delegated subnet/NSG, proves the gateway reaches the current backend, and emits the verified URL without changing Container Apps public access.
- **`deploy-frontend`** — builds the frontend against that exact gateway job output and deploys it with `@azure/static-web-apps-cli` using a stored deployment token. It no longer trusts a separately maintained production API URL variable.
- **`deploy-backend`** — waits for the frontend cutover, builds the commit-pinned FastAPI image, prepares private DNS, disables Container Apps public access, creates/verifies the private endpoint, proves APIM still works and direct ingress is denied, then runs `deploy_backend.ps1` so the image, retired-sidecar removal, CORS, probes, and ingress are updated atomically and verified through APIM.

**One-time setup** (this is specific to running YOUR OWN fork's CI/CD against YOUR OWN subscription — not required for the quickstart script or a one-off manual deployment):

1. A dedicated app registration (`genie-github-actions-deploy`, no client secret) holds a **federated identity credential** trusting your repo's GitHub Actions OIDC issuer, scoped to the `production` GitHub Environment — narrower than a branch-based subject, since it also requires the workflow job to declare `environment: production`. **Important**: the subject must match GitHub's *actual* token claim exactly, which is `repo:<org>/<repo>:environment:<env>` only if the org/repo have never been renamed — if either has been renamed, GitHub appends numeric IDs instead (`repo:<org>@<orgId>/<repo>@<repoId>:environment:<env>`). Get the exact value from a failed `azure/login@v2` run's log line `Federated token details: ... subject claim - ...` if login fails with `AADSTS700213`.
2. That identity's service principal holds three least-privilege assignments (never a subscription- or resource-group-wide Owner/Contributor grant):
   - **Container Registry Tasks Contributor**, scoped to just the ACR resource — covers `az acr build`'s scheduleRun/upload actions without granting registry data-plane push/pull.
   - **Container Apps Contributor**, scoped to just the backend Container App resource — covers the atomic ARM patch.
   - **Genie Platform Gateway Deployer**, scoped to the Genie resource group — a custom role containing only resource-group deployment, APIM API, delegated subnet/NSG, VNet join, private endpoint/DNS, and Container Apps environment update/approval actions. It contains no delete or authorization-management action. Create/update and assign it once with `scripts/configure_platform_gateway_deployer.ps1 -SubscriptionId <id> -ResourceGroup <rg> -PrincipalObjectId <oidc-service-principal-object-id>`.
3. The **runtime Genie backend managed identity** has the custom `Genie Prototype Resource Group Operator` role plus API Management Service Contributor, Network Contributor, Container Apps Contributor, Managed Identity Contributor, and Managed Identity Operator at subscription scope. The custom role permits resource-group lifecycle plus only the managed-environment create/read and operation-status actions missing from Azure's built-in Container Apps Contributor role; the built-in roles remain restricted to their respective provider surfaces. Shared ACR and role-assignment permissions remain constrained to existing resource scopes. New prototypes do not require Microsoft Graph application writes. Create/update and assign the custom role with `scripts/configure_prototype_operator.ps1 -SubscriptionId <id> -PrincipalObjectId <runtime-managed-identity-object-id>`.
4. Your fork's **Settings → Secrets and variables → Actions** has:
   - **Secrets**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (identify the federated deployment app, not credentials by themselves), `GENIE_GITHUB_MCP_TOKEN`, `SWA_DEPLOYMENT_TOKEN`, `GENIE_APIM_SUBSCRIPTION_KEY` (the platform APIM's shared access key - generate a strong random value, e.g. `openssl rand -base64 32`), `GENIE_AUTH_TOKEN_SIGNING_KEY` (at least 32 random bytes, e.g. `openssl rand -base64 48`), and `GENIE_AUTH_USERS` (one `username:pbkdf2_sha256$iterations$salt_hex$hash_hex` line per account - see `app.security.password_hashing.hash_password`).
   - **Variables**: `AZURE_ACR_NAME`, `AZURE_CONTAINER_APP_NAME`, `AZURE_RESOURCE_GROUP`, `GENIE_GATEWAY_ALLOWED_ORIGIN`, `GENIE_MEMORY_STORE_ENDPOINT`, `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_EMAIL`, `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_NAME`, and `GENIE_PROTOTYPE_MAX_ACTIVE_PER_OWNER` (`0` for unlimited). `VITE_GENIE_API_BASE_URL` is not a production GitHub variable; `prepare-gateway` emits it from the APIM deployment.

If this identity/RBAC/secrets setup is ever missing or revoked, `deploy-backend`/`deploy-frontend` fail fast (within seconds, at an explicit "Check required secrets" step) rather than hanging — the `backend`/`frontend` test jobs are unaffected either way and still gate every PR.
