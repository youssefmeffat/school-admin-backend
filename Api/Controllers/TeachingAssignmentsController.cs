using Api.Extensions;
using Application.Common;
using Application.DTOs;
using Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class TeachingAssignmentsController : ControllerBase
    {
        private readonly ITeachingAssignmentsService _assignments;

        public TeachingAssignmentsController(ITeachingAssignmentsService assignments) => _assignments = assignments;

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<TeachingAssignmentDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll([FromQuery] PaginationParams pagination) =>
            Ok(await _assignments.GetAllAsync(pagination));

        [HttpGet("teacher/{teacherId:int}")]
        [ProducesResponseType(typeof(List<TeachingAssignmentDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetForTeacher(int teacherId)
        {
            var result = await _assignments.GetForTeacherAsync(teacherId);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpGet("subject/{subjectId:int}")]
        [ProducesResponseType(typeof(List<TeachingAssignmentDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetForSubject(int subjectId)
        {
            var result = await _assignments.GetForSubjectAsync(subjectId);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpGet("available-classes/{subjectId:int}")]
        [ProducesResponseType(typeof(List<AvailableClassDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetAvailableClasses(int subjectId)
        {
            var result = await _assignments.GetAvailableClassesAsync(subjectId);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(TeachingAssignmentDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        [ProducesResponseType(StatusCodes.Status409Conflict)]
        public async Task<IActionResult> Create([FromBody] TeachingAssignmentCreateDto dto)
        {
            if (!ModelState.IsValid) return BadRequest(ModelState);

            var result = await _assignments.CreateAsync(dto);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _assignments.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
