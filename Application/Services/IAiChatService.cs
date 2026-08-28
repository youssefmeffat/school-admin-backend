using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface IAiChatService
    {
        Task<ServiceResult<AiChatConnectResponse>> ConnectAsync(string sessionId);

        Task<ServiceResult<AiChatAskResponse>> AskAsync(
            string sessionId,
            string question,
            string? conversationContext = null);

        Task<ServiceResult> CloseSessionAsync(string sessionId);

        Task<bool> IsPythonServiceHealthyAsync();
    }
}
