using Application.Common;
using Microsoft.AspNetCore.Mvc;

namespace Api.Extensions
{
    public static class ServiceErrorExtensions
    {
        public static ActionResult ToActionResult(this ServiceError error) => error.Kind switch
        {
            ServiceErrorKind.NotFound => error.Body is null
                ? new NotFoundResult()
                : new NotFoundObjectResult(error.Body),

            ServiceErrorKind.Validation => error.Body is null
                ? new BadRequestResult()
                : new BadRequestObjectResult(error.Body),

            ServiceErrorKind.Conflict => error.Body is null
                ? new StatusCodeResult(StatusCodes.Status409Conflict)
                : new ConflictObjectResult(error.Body),

            _ => new StatusCodeResult(StatusCodes.Status500InternalServerError)
        };
    }
}
