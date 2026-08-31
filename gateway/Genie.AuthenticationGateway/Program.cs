using Microsoft.AspNetCore.Authorization;
using Microsoft.Identity.ServiceEssentials;
using Microsoft.Identity.ServiceEssentials.Authentication;
using Microsoft.Identity.ServiceEssentials.Configuration;
using Yarp.ReverseProxy.Forwarder;

const string BackendDestination = "Backend:Destination";
const string AzureAdSection = "AzureAd";

var builder = WebApplication.CreateBuilder(args);
var azureAd = builder.Configuration.GetRequiredSection(AzureAdSection);
var instance = GetRequiredValue(azureAd, "Instance");
var tenantId = GetRequiredValue(azureAd, "TenantId");
var clientId = GetRequiredValue(azureAd, "ClientId");
var audiences = azureAd.GetSection("Audiences").Get<string[]>() ?? [clientId, $"api://{clientId}"];
var allowedOrigins = builder.Configuration.GetSection("Cors:AllowedOrigins").Get<string[]>() ?? [];
if (allowedOrigins.Length == 0 || allowedOrigins.Any(string.IsNullOrWhiteSpace))
{
    throw new InvalidOperationException("Cors:AllowedOrigins must contain at least one origin.");
}

builder.Services
    .AddAuthentication(MiseAuthenticationDefaults.AuthenticationScheme)
    .AddMiseWithDefaultModules(
        builder.Configuration,
        options =>
        {
            options.AzureAd ??= new MiseAuthenticationOptions();
            options.AzureAd.Instance = instance;
            options.AzureAd.TenantId = tenantId;
            options.AzureAd.ClientId = clientId;
            options.AzureAd.Audience = clientId;
            options.AzureAd.Audiences = audiences;

            var tokenTypes = new TokenTypeOptions
            {
                AppToken = true,
                UserToken = true,
            };
            var bearerProtocol = new ProtocolOptions();
            bearerProtocol.TokenTypes["AccessToken"] = tokenTypes;
            var inboundPolicy = new MiseInboundPolicyOptions
            {
                Label = "GenieGatewayInbound",
                Instance = instance,
                TenantId = tenantId,
                Audiences = audiences,
            };
            inboundPolicy.Protocols[
                Microsoft.Identity.ServiceEssentials.Authentication.Protocol.BearerConstants.ProtocolName
            ] = bearerProtocol;
            options.AzureAd.InboundPolicies = [inboundPolicy];
        },
        MiseAuthenticationDefaults.AuthenticationScheme);

builder.Services.AddAuthorization(options =>
{
    options.FallbackPolicy = new AuthorizationPolicyBuilder()
        .RequireAuthenticatedUser()
        .Build();
});
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
    {
        policy.WithOrigins(allowedOrigins)
            .AllowAnyHeader()
            .AllowAnyMethod()
            .WithExposedHeaders("X-Correlation-Id");
    });
});
builder.Services.AddHttpClient();
builder.Services.AddHttpForwarder();

var app = builder.Build();
app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/health/live", () => Results.Ok(new { status = "ok" })).AllowAnonymous();
app.MapGet("/health/ready", async (IHttpClientFactory httpClientFactory, CancellationToken cancellationToken) =>
{
    try
    {
        var client = httpClientFactory.CreateClient();
        var destination = builder.Configuration[BackendDestination]
            ?? throw new InvalidOperationException($"{BackendDestination} is required.");
        using var response = await client.GetAsync(new Uri(new Uri(destination), "/health/ready"), cancellationToken);
        return response.IsSuccessStatusCode
            ? Results.Ok(new { status = "ready" })
            : Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
    }
    catch (HttpRequestException)
    {
        return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
    }
    catch (TaskCanceledException)
    {
        return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
    }
}).AllowAnonymous();

app.MapForwarder("/{**path}", builder.Configuration[BackendDestination]
        ?? throw new InvalidOperationException($"{BackendDestination} is required."))
    .RequireAuthorization();

app.Run();

static string GetRequiredValue(IConfiguration configuration, string key)
{
    var value = configuration[key];
    return string.IsNullOrWhiteSpace(value)
        ? throw new InvalidOperationException($"AzureAd:{key} is required.")
        : value;
}

public partial class Program
{
}