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
    public class SubjectsController : ControllerBase
    {
        private readonly ISubjectsService _subjects;

        public SubjectsController(ISubjectsService subjects)
        {
            _subjects = subjects;
        }

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<SubjectDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll([FromQuery] PaginationParams pagination)
        {
            return Ok(await _subjects.GetAllAsync(pagination));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(SubjectDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _subjects.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(SubjectDto), StatusCodes.Status201Created)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Create([FromBody] SubjectCreateDto dto)
        {
            if (!ModelState.IsValid) return BadRequest(ModelState);

            var subject = await _subjects.CreateAsync(dto);
            return CreatedAtAction(nameof(Get), new { id = subject.Id }, subject);
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(int id, [FromBody] SubjectCreateDto dto)
        {
            var result = await _subjects.UpdateAsync(id, dto);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _subjects.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
