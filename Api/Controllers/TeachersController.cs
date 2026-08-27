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
    public class TeachersController : ControllerBase
    {
        private readonly ITeachersService _teachers;

        public TeachersController(ITeachersService teachers)
        {
            _teachers = teachers;
        }

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<TeacherDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll(
            [FromQuery] PaginationParams pagination,
            [FromQuery] string? search = null)
        {
            return Ok(await _teachers.GetAllAsync(pagination, search));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(TeacherDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _teachers.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(TeacherDto), StatusCodes.Status201Created)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Create([FromBody] TeacherCreateDto dto)
        {
            if (!ModelState.IsValid) return BadRequest(ModelState);

            var teacher = await _teachers.CreateAsync(dto);
            return CreatedAtAction(nameof(Get), new { id = teacher.Id }, teacher);
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(int id, [FromBody] TeacherCreateDto dto)
        {
            var result = await _teachers.UpdateAsync(id, dto);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _teachers.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}