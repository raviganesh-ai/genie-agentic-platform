using System.Net;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace Genie.AuthenticationGateway.Tests;

public sealed class GatewaySecurityTests : IClassFixture<GatewayFactory>
{
    private readonly HttpClient _client;

    public GatewaySecurityTests(GatewayFactory factory)
    {
        _client = factory.CreateClient(new WebApplicationFactoryClientOptions
        {
            AllowAutoRedirect = false,
        });
    }

    [Fact]
    public async Task LivenessIsAnonymous()
    {
        using var response = await _client.GetAsync("/health/live");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task ReadinessFailsClosedWhenBackendIsUnavailable()
    {
        using var response = await _client.GetAsync("/health/ready");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
    }

    [Fact]
    public async Task CorsPreflightIsAllowedForConfiguredOrigin()
    {
        using var request = new HttpRequestMessage(HttpMethod.Options, "/sessions");
        request.Headers.Add("Origin", GatewayFactory.AllowedOrigin);
        request.Headers.Add("Access-Control-Request-Method", "GET");

        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
        Assert.Equal(
            GatewayFactory.AllowedOrigin,
            Assert.Single(response.Headers.GetValues("Access-Control-Allow-Origin")));
    }

    [Fact]
    public async Task ApiRequestWithoutTokenIsRejectedBeforeProxying()
    {
        using var response = await _client.GetAsync("/sessions");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }
}

public sealed class GatewayFactory : WebApplicationFactory<Program>
{
    public const string AllowedOrigin = "https://genie.example";

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("AzureAd:Instance", "https://login.microsoftonline.com/");
        builder.UseSetting("AzureAd:TenantId", "00000000-0000-0000-0000-000000000001");
        builder.UseSetting("AzureAd:ClientId", "00000000-0000-0000-0000-000000000002");
        builder.UseSetting("AzureAd:Audiences:0", "00000000-0000-0000-0000-000000000002");
        builder.UseSetting("Cors:AllowedOrigins:0", AllowedOrigin);
        builder.UseSetting("Backend:Destination", "http://127.0.0.1:1");
    }
}