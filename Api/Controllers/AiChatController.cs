using Api.Extensions;
using Application.DTOs;
using Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class AiChatController : ControllerBase
    {
        private readonly IAiChatService _aiChat;

        public AiChatController(IAiChatService aiChat)
        {
            _aiChat = aiChat;
        }

        [HttpGet("health")]
        [ProducesResponseType(StatusCodes.Status200OK)]
        public async Task<IActionResult> Health()
        {
            var healthy = await _aiChat.IsPythonServiceHealthyAsync();

            return Ok(new
            {
                success = healthy,
                message = healthy
                    ? "AI assistant is available."
                    : "AI assistant is currently unavailable."
            });
        }

        [HttpPost("connect")]
        [ProducesResponseType(typeof(AiChatConnectResponse), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Connect([FromBody] AiChatConnectRequest request)
        {
            var result = await _aiChat.ConnectAsync(request.SessionId);

            return result.Succeeded
                ? Ok(result.Value)
                : result.Error!.ToActionResult();
        }

        [HttpPost("ask")]
        [ProducesResponseType(typeof(AiChatAskResponse), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Ask([FromBody] AiChatAskRequest request)
        {
            var result = await _aiChat.AskAsync(
                request.SessionId,
                request.Question,
                request.ConversationContext);

            return result.Succeeded
                ? Ok(result.Value)
                : result.Error!.ToActionResult();
        }

        [HttpDelete("session/{sessionId}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> CloseSession(string sessionId)
        {
            var result = await _aiChat.CloseSessionAsync(sessionId);

            return result.Succeeded
                ? NoContent()
                : result.Error!.ToActionResult();
        }
    }
}
