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
    public class ExamsController : ControllerBase
    {
        private readonly IExamsService _exams;

        public ExamsController(IExamsService exams)
        {
            _exams = exams;
        }

        [HttpGet]
        [ProducesResponseType(typeof(PagedResult<ExamDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetAll(
            [FromQuery] PaginationParams pagination,
            [FromQuery] string? search = null,
            [FromQuery] string? status = null)
        {
            return Ok(await _exams.GetAllAsync(pagination, search, status));
        }

        [HttpGet("{id:int}")]
        [ProducesResponseType(typeof(ExamDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Get(int id)
        {
            var result = await _exams.GetAsync(id);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost]
        [ProducesResponseType(typeof(ExamDto), StatusCodes.Status201Created)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        public async Task<IActionResult> Create([FromBody] ExamCreateDto dto)
        {
            if (!ModelState.IsValid)
                return BadRequest(ModelState);

            var result = await _exams.CreateAsync(dto);
            if (!result.Succeeded)
                return result.Error!.ToActionResult();

            return CreatedAtAction(nameof(Get), new { id = result.Value!.Id }, result.Value);
        }

        [HttpPut("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Update(
            int id,
            [FromBody] ExamCreateDto dto)
        {
            if (!ModelState.IsValid)
                return BadRequest(ModelState);

            var result = await _exams.UpdateAsync(id, dto);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }

        [HttpDelete("{id:int}")]
        [ProducesResponseType(StatusCodes.Status204NoContent)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Delete(int id)
        {
            var result = await _exams.DeleteAsync(id);
            return result.Succeeded ? NoContent() : result.Error!.ToActionResult();
        }
    }
}
