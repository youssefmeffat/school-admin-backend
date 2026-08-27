using Api.Extensions;
using Application.Common;
using Application.DTOs;
using Application.Services;
using Core.Models;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class StudentsController : ControllerBase
    {
        private readonly IStudentsService _students;

        public StudentsController(IStudentsService students)
        {
            _students = students;
        }

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<StudentSummaryDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll(
            [FromQuery] PaginationParams pagination,
            [FromQuery] string? search = null)
        {
            return Ok(await _students.GetAllAsync(pagination, search));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(StudentSummaryDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _students.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(Student), StatusCodes.Status201Created)]
        public async Task<IActionResult> Create([FromBody] Student student)
        {
            var created = await _students.CreateAsync(student);
            return CreatedAtAction(nameof(Get), new { id = created.Id }, created);
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(int id, [FromBody] Student updated)
        {
            var result = await _students.UpdateAsync(id, updated);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _students.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
