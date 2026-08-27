namespace Application.Common
{
    public enum ServiceErrorKind
    {
        NotFound,
        Validation,
        Conflict
    }

    // Body carries the exact response payload (string, anonymous object, or null for an empty body),
    // so controllers can reproduce the response shape the old inline logic used to return.
    public sealed class ServiceError
    {
        public ServiceErrorKind Kind { get; }
        public object? Body { get; }

        private ServiceError(ServiceErrorKind kind, object? body)
        {
            Kind = kind;
            Body = body;
        }

        public static ServiceError NotFound(object? body = null) => new(ServiceErrorKind.NotFound, body);
        public static ServiceError Validation(object? body) => new(ServiceErrorKind.Validation, body);
        public static ServiceError Conflict(object? body) => new(ServiceErrorKind.Conflict, body);
    }

    public sealed class ServiceResult<T>
    {
        public T? Value { get; }
        public ServiceError? Error { get; }
        public bool Succeeded => Error is null;

        private ServiceResult(T? value, ServiceError? error)
        {
            Value = value;
            Error = error;
        }

        public static ServiceResult<T> Success(T value) => new(value, null);
        public static ServiceResult<T> Fail(ServiceError error) => new(default, error);
    }

    public sealed class ServiceResult
    {
        public ServiceError? Error { get; }
        public bool Succeeded => Error is null;

        private ServiceResult(ServiceError? error)
        {
            Error = error;
        }

        public static ServiceResult Success() => new(null);
        public static ServiceResult Fail(ServiceError error) => new(error);
    }
}
