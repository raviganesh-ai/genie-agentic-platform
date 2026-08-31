FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

ARG AZURE_DEVOPS_TOKEN
RUN curl -fsSL https://aka.ms/install-artifacts-credprovider.sh | sh

COPY gateway/NuGet.config gateway/NuGet.config
COPY gateway/Genie.AuthenticationGateway/Genie.AuthenticationGateway.csproj gateway/Genie.AuthenticationGateway/
RUN VSS_NUGET_EXTERNAL_FEED_ENDPOINTS="{\"endpointCredentials\":[{\"endpoint\":\"https://pkgs.dev.azure.com/MicrosoftIT/OneITVSO/_packaging/PS-GCM-FieldExperiencePlatform/nuget/v3/index.json\",\"username\":\"AzureDevOps\",\"password\":\"${AZURE_DEVOPS_TOKEN}\"}]}" \
    dotnet restore gateway/Genie.AuthenticationGateway/Genie.AuthenticationGateway.csproj \
    --configfile gateway/NuGet.config

COPY gateway/Genie.AuthenticationGateway/ gateway/Genie.AuthenticationGateway/
RUN dotnet publish gateway/Genie.AuthenticationGateway/Genie.AuthenticationGateway.csproj \
    --configuration Release \
    --no-restore \
    --output /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /app/publish .

ENV ASPNETCORE_HTTP_PORTS=8080
EXPOSE 8080
USER $APP_UID
ENTRYPOINT ["dotnet", "Genie.AuthenticationGateway.dll"]