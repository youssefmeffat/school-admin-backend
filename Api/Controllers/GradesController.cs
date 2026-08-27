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
    public class GradesController : ControllerBase
    {
        private readonly IGradesService _grades;

        public GradesController(IGradesService grades)
        {
            _grades = grades;
        }

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<GradeLevelDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll(
            [FromQuery] PaginationParams pagination,
            [FromQuery] string? search = null)
        {
            return Ok(await _grades.GetAllAsync(pagination, search));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(GradeLevelDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _grades.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(Grade), StatusCodes.Status201Created)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Create([FromBody] Grade grade)
        {
            if (!ModelState.IsValid)
                return BadRequest(ModelState);

            var result = await _grades.CreateAsync(grade);

            if (!result.Succeeded)
                return result.Error!.ToActionResult();

            return CreatedAtAction(
                nameof(Get),
                new { id = result.Value!.Id },
                result.Value
            );
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(int id, [FromBody] Grade updated)
        {
            var result = await _grades.UpdateAsync(id, updated);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _grades.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
