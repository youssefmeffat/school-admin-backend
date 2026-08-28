using System.Text.Json;
using System.Text.Json.Serialization;

namespace Application.DTOs
{
    public class AiChatConnectRequest
    {
        public string SessionId { get; set; } = string.Empty;
    }

    public class AiChatAskRequest
    {
        public string SessionId { get; set; } = string.Empty;
        public string Question { get; set; } = string.Empty;
        public string? ConversationContext { get; set; }
    }

    public class AiChatConnectResponse
    {
        public bool Success { get; set; }
        public string SessionId { get; set; } = string.Empty;
        public List<string> Tables { get; set; } = new();
        public string Message { get; set; } = string.Empty;
    }

    public class AiChatAskResponse
    {
        public bool Success { get; set; }
        public string Answer { get; set; } = string.Empty;
        public string? Sql { get; set; }
        public List<string> TablesUsed { get; set; } = new();
        public List<Dictionary<string, JsonElement>> Rows { get; set; } = new();
        public int RepairAttempts { get; set; }
        public bool FromCache { get; set; }
    }

    internal class PythonConnectRequest
    {
        [JsonPropertyName("connection_string")]
        public string ConnectionString { get; set; } = string.Empty;
    }

    internal class PythonConnectResponse
    {
        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("session_id")]
        public string SessionId { get; set; } = string.Empty;

        [JsonPropertyName("tables")]
        public List<string> Tables { get; set; } = new();

        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;
    }

    internal class PythonAskRequest
    {
        [JsonPropertyName("question")]
        public string Question { get; set; } = string.Empty;

        [JsonPropertyName("conversation_context")]
        public string? ConversationContext { get; set; }
    }

    internal class PythonAskResponse
    {
        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("answer")]
        public string? Answer { get; set; }

        [JsonPropertyName("sql")]
        public string? Sql { get; set; }

        [JsonPropertyName("tables_used")]
        public List<string>? TablesUsed { get; set; }

        [JsonPropertyName("rows")]
        public List<Dictionary<string, JsonElement>>? Rows { get; set; }

        [JsonPropertyName("repair_attempts")]
        public int RepairAttempts { get; set; }

        [JsonPropertyName("from_cache")]
        public bool FromCache { get; set; }
    }

    internal class PythonErrorResponse
    {
        [JsonPropertyName("detail")]
        public string? Detail { get; set; }
    }
}