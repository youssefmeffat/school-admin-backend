using Api.Extensions;
using Application.DTOs;
using Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class GradeSubjectsController : ControllerBase
    {
        private readonly IGradeSubjectsService _gradeSubjects;

        public GradeSubjectsController(IGradeSubjectsService gradeSubjects)
        {
            _gradeSubjects = gradeSubjects;
        }

        [HttpGet("grade/{gradeId:int}")]
        [ProducesResponseType(typeof(List<GradeSubjectDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetForGrade(int gradeId)
        {
            var result = await _gradeSubjects.GetForGradeAsync(gradeId);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(GradeSubjectDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Add([FromBody] GradeSubjectRequest request)
        {
            var result = await _gradeSubjects.AddAsync(request);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Remove(int id)
        {
            var result = await _gradeSubjects.RemoveAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
