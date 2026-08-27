using Application.DTOs;
using Application.Services;
using Core.Models;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class StudentExamsController : ControllerBase
    {
        private readonly IStudentExamService _service;

        public StudentExamsController(
            IStudentExamService service)
        {
            _service = service;
        }

        [HttpPost("enroll")]
        [ProducesResponseType(StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> Enroll(
            [FromQuery] int studentId,
            [FromQuery] int examId)
        {
            try
            {
                await _service.EnrollStudentAsync(
                    studentId,
                    examId
                );

                return Ok();
            }
            catch (KeyNotFoundException ex)
            {
                return NotFound(ex.Message);
            }
        }

        [HttpPut("score")]
        [ProducesResponseType(StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status400BadRequest)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> SetScore(
            [FromQuery] int studentId,
            [FromQuery] int examId,
            [FromQuery] decimal score)
        {
            try
            {
                await _service.SetScoreAsync(
                    studentId,
                    examId,
                    score
                );

                return Ok();
            }
            catch (KeyNotFoundException ex)
            {
                return NotFound(ex.Message);
            }
            catch (ArgumentOutOfRangeException ex)
            {
                return BadRequest(ex.Message);
            }
        }

        [HttpGet("exam/{examId:int}/participants")]
        [ProducesResponseType(typeof(List<ExamParticipantDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetParticipants(
            int examId)
        {
            try
            {
                var list =
                    await _service
                        .GetExamParticipantsAsync(
                            examId
                        );

                return Ok(list);
            }
            catch (KeyNotFoundException ex)
            {
                return NotFound(ex.Message);
            }
        }

        [HttpGet("student/{studentId:int}")]
        [ProducesResponseType(typeof(List<StudentExamResult>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetStudentExams(
            int studentId)
        {
            var list =
                await _service
                    .GetStudentExamsAsync(
                        studentId
                    );

            return Ok(list);
        }
    }
}
