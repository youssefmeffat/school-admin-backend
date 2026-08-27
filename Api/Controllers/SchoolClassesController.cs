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
    public class SchoolClassesController : ControllerBase
    {
        private readonly ISchoolClassesService _classes;

        public SchoolClassesController(ISchoolClassesService classes) => _classes = classes;

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<ClassSummaryDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll(
            [FromQuery] PaginationParams pagination,
            [FromQuery] string? search = null)
        {
            return Ok(await _classes.GetAllAsync(pagination, search));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(ClassSummaryDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _classes.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(Class), StatusCodes.Status201Created)]
        public async Task<IActionResult> Create([FromBody] Class cls)
        {
            var created = await _classes.CreateAsync(cls);
            return CreatedAtAction(nameof(Get), new { id = created.Id }, created);
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(int id, [FromBody] Class updated)
        {
            var result = await _classes.UpdateAsync(id, updated);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpPut("{id:int}/students")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> UpdateStudents(
            int id,
            [FromBody] UpdateClassStudentsRequest request)
        {
            var result = await _classes.UpdateStudentsAsync(id, request);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _classes.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}