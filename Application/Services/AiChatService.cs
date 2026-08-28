using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Application.Common;
using Application.DTOs;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace Application.Services
{
    public class AiChatService : IAiChatService
    {
        private readonly HttpClient _http;
        private readonly IConfiguration _configuration;
        private readonly ILogger<AiChatService> _logger;

        private static readonly JsonSerializerOptions JsonOptions =
            new(JsonSerializerDefaults.Web);

        public AiChatService(
            HttpClient http,
            IConfiguration configuration,
            ILogger<AiChatService> logger)
        {
            _http = http;
            _configuration = configuration;
            _logger = logger;
        }

        public async Task<ServiceResult<AiChatConnectResponse>> ConnectAsync(
            string sessionId)
        {
            sessionId = sessionId?.Trim() ?? string.Empty;

            if (string.IsNullOrWhiteSpace(sessionId))
            {
                return ServiceResult<AiChatConnectResponse>.Fail(
                    ServiceError.Validation("A chat session ID is required."));
            }

            var connectionString =
                _configuration.GetConnectionString("DefaultConnection");

            if (string.IsNullOrWhiteSpace(connectionString))
            {
                _logger.LogError("DefaultConnection is missing from configuration.");

                return ServiceResult<AiChatConnectResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant is not configured for the school database."));
            }

            try
            {
                using var response = await _http.PostAsJsonAsync(
                    $"/sessions/{Uri.EscapeDataString(sessionId)}/connect",
                    new PythonConnectRequest
                    {
                        ConnectionString = connectionString
                    });

                if (!response.IsSuccessStatusCode)
                {
                    return ServiceResult<AiChatConnectResponse>.Fail(
                        ServiceError.Validation(
                            await ReadPythonErrorAsync(response)));
                }

                var python = await response.Content
                    .ReadFromJsonAsync<PythonConnectResponse>(JsonOptions);

                if (python == null)
                {
                    return ServiceResult<AiChatConnectResponse>.Fail(
                        ServiceError.Validation(
                            "The AI assistant returned an invalid response."));
                }

                return ServiceResult<AiChatConnectResponse>.Success(
                    new AiChatConnectResponse
                    {
                        Success = python.Success,
                        SessionId = python.SessionId,
                        Tables = python.Tables ?? new List<string>(),
                        Message = string.IsNullOrWhiteSpace(python.Message)
                            ? "The AI assistant is ready."
                            : python.Message
                    });
            }
            catch (TaskCanceledException ex)
            {
                _logger.LogWarning(ex, "Text2SQL connect timed out.");

                return ServiceResult<AiChatConnectResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant took too long to connect. Please try again."));
            }
            catch (HttpRequestException ex)
            {
                _logger.LogWarning(ex, "Could not reach Python Text2SQL.");

                return ServiceResult<AiChatConnectResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant is currently unavailable."));
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Unexpected Text2SQL connect failure.");

                return ServiceResult<AiChatConnectResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant could not be started."));
            }
        }

        public async Task<ServiceResult<AiChatAskResponse>> AskAsync(
            string sessionId,
            string question,
            string? conversationContext = null)
        {
            sessionId = sessionId?.Trim() ?? string.Empty;
            question = question?.Trim() ?? string.Empty;

            if (string.IsNullOrWhiteSpace(sessionId))
            {
                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation("A chat session ID is required."));
            }

            if (string.IsNullOrWhiteSpace(question))
            {
                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation("Please enter a question."));
            }

            if (question.Length > 2000)
            {
                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation(
                        "Your question is too long. Please keep it under 2000 characters."));
            }

            try
            {
                var response = await SendAskAsync(
                    sessionId,
                    question,
                    conversationContext);

                // Python keeps sessions in memory. If it restarted, reconnect
                // automatically and retry this question once.
                if (response.StatusCode == HttpStatusCode.NotFound)
                {
                    response.Dispose();

                    var reconnect = await ConnectAsync(sessionId);

                    if (!reconnect.Succeeded)
                    {
                        return ServiceResult<AiChatAskResponse>.Fail(
                            reconnect.Error!);
                    }

                    response = await SendAskAsync(
                        sessionId,
                        question,
                        conversationContext);
                }

                using (response)
                {
                    if (!response.IsSuccessStatusCode)
                    {
                        return ServiceResult<AiChatAskResponse>.Fail(
                            ServiceError.Validation(
                                await ReadPythonErrorAsync(response)));
                    }

                    var python = await response.Content
                        .ReadFromJsonAsync<PythonAskResponse>(JsonOptions);

                    if (python == null)
                    {
                        return ServiceResult<AiChatAskResponse>.Fail(
                            ServiceError.Validation(
                                "The AI assistant returned an invalid response."));
                    }

                    return ServiceResult<AiChatAskResponse>.Success(
                        new AiChatAskResponse
                        {
                            Success = python.Success,
                            Answer = string.IsNullOrWhiteSpace(python.Answer)
                                ? "I couldn't find an answer to that."
                                : python.Answer,
                            Sql = python.Sql,
                            TablesUsed = python.TablesUsed ?? new List<string>(),
                            Rows = python.Rows ??
                                new List<Dictionary<string, JsonElement>>(),
                            RepairAttempts = python.RepairAttempts,
                            FromCache = python.FromCache
                        });
                }
            }
            catch (TaskCanceledException ex)
            {
                _logger.LogWarning(ex, "Text2SQL ask timed out.");

                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant took too long to answer. Please try again."));
            }
            catch (HttpRequestException ex)
            {
                _logger.LogWarning(ex, "Could not reach Python Text2SQL.");

                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant is currently unavailable."));
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Unexpected Text2SQL ask failure.");

                return ServiceResult<AiChatAskResponse>.Fail(
                    ServiceError.Validation(
                        "The AI assistant could not answer that question."));
            }
        }

        public async Task<ServiceResult> CloseSessionAsync(string sessionId)
        {
            sessionId = sessionId?.Trim() ?? string.Empty;

            if (string.IsNullOrWhiteSpace(sessionId))
            {
                return ServiceResult.Fail(
                    ServiceError.Validation("A chat session ID is required."));
            }

            try
            {
                using var response = await _http.DeleteAsync(
                    $"/sessions/{Uri.EscapeDataString(sessionId)}");

                if (!response.IsSuccessStatusCode)
                {
                    return ServiceResult.Fail(
                        ServiceError.Validation(
                            await ReadPythonErrorAsync(response)));
                }

                return ServiceResult.Success();
            }
            catch (HttpRequestException ex)
            {
                _logger.LogWarning(ex, "Could not close Text2SQL session.");

                return ServiceResult.Fail(
                    ServiceError.Validation(
                        "The AI session could not be closed."));
            }
        }

        public async Task<bool> IsPythonServiceHealthyAsync()
        {
            try
            {
                using var response = await _http.GetAsync("/health");
                return response.IsSuccessStatusCode;
            }
            catch
            {
                return false;
            }
        }

        private Task<HttpResponseMessage> SendAskAsync(
            string sessionId,
            string question,
            string? conversationContext)
        {
            return _http.PostAsJsonAsync(
                $"/sessions/{Uri.EscapeDataString(sessionId)}/ask",
                new PythonAskRequest
                {
                    Question = question,
                    ConversationContext =
                        string.IsNullOrWhiteSpace(conversationContext)
                            ? null
                            : conversationContext.Trim()
                });
        }

        private static async Task<string> ReadPythonErrorAsync(
            HttpResponseMessage response)
        {
            try
            {
                var error = await response.Content
                    .ReadFromJsonAsync<PythonErrorResponse>(JsonOptions);

                if (!string.IsNullOrWhiteSpace(error?.Detail))
                    return error.Detail;
            }
            catch
            {
            }

            return response.StatusCode == HttpStatusCode.NotFound
                ? "The AI chat session was not found."
                : "The AI assistant could not process that request.";
        }
    }
}