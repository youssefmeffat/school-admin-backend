using Api.Extensions;
using Application.DTOs;
using Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class AttendanceController : ControllerBase
    {
        private readonly IAttendanceService _attendance;

        public AttendanceController(IAttendanceService attendance)
        {
            _attendance = attendance;
        }

        [HttpGet("students")]
        [ProducesResponseType(typeof(List<StudentAttendanceListItemDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetStudentAttendance(
            [FromQuery] DateTime date,
            [FromQuery] int? studentId = null,
            [FromQuery] int? gradeId = null,
            [FromQuery] int? classId = null)
        {
            var students = await _attendance.GetStudentAttendanceAsync(date, studentId, gradeId, classId);
            return Ok(students);
        }

        [HttpGet("students/{studentId:int}")]
        [ProducesResponseType(typeof(List<StudentAttendanceHistoryDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetStudentHistory(
            int studentId,
            [FromQuery] DateTime? from = null,
            [FromQuery] DateTime? to = null)
        {
            var result = await _attendance.GetStudentHistoryAsync(studentId, from, to);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost("students")]
        [ProducesResponseType(typeof(SavedStudentAttendanceDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> SaveStudentAttendance(
            [FromBody] StudentAttendanceRequest request)
        {
            var result = await _attendance.SaveStudentAttendanceAsync(request);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost("students/generate")]
        [ProducesResponseType(typeof(GenerateAttendanceResultDto), StatusCodes.Status200OK)]
        public async Task<IActionResult> GenerateStudentAttendance(
            [FromBody] GenerateAttendanceRequest request)
        {
            var result = await _attendance.GenerateStudentAttendanceAsync(request);
            return Ok(result);
        }

        [HttpPost("students/bulk")]
        [ProducesResponseType(typeof(BulkAttendanceSaveResultDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> SaveStudentAttendanceBulk(
            [FromBody] BulkStudentAttendanceRequest request)
        {
            var result = await _attendance.SaveStudentAttendanceBulkAsync(request);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpGet("teachers")]
        [ProducesResponseType(typeof(List<TeacherAttendanceListItemDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetTeacherAttendance(
            [FromQuery] DateTime date,
            [FromQuery] int? teacherId = null)
        {
            var teachers = await _attendance.GetTeacherAttendanceAsync(date, teacherId);
            return Ok(teachers);
        }

        [HttpGet("teachers/{teacherId:int}")]
        [ProducesResponseType(typeof(List<TeacherAttendanceHistoryDto>), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> GetTeacherHistory(
            int teacherId,
            [FromQuery] DateTime? from = null,
            [FromQuery] DateTime? to = null)
        {
            var result = await _attendance.GetTeacherHistoryAsync(teacherId, from, to);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost("teachers")]
        [ProducesResponseType(typeof(SavedTeacherAttendanceDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> SaveTeacherAttendance(
            [FromBody] TeacherAttendanceRequest request)
        {
            var result = await _attendance.SaveTeacherAttendanceAsync(request);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }

        [HttpPost("teachers/generate")]
        [ProducesResponseType(typeof(GenerateAttendanceResultDto), StatusCodes.Status200OK)]
        public async Task<IActionResult> GenerateTeacherAttendance(
            [FromBody] GenerateAttendanceRequest request)
        {
            var result = await _attendance.GenerateTeacherAttendanceAsync(request);
            return Ok(result);
        }

        [HttpPost("teachers/bulk")]
        [ProducesResponseType(typeof(BulkAttendanceSaveResultDto), StatusCodes.Status200OK)]
        [ProducesResponseType(StatusCodes.Status404NotFound)]
        public async Task<IActionResult> SaveTeacherAttendanceBulk(
            [FromBody] BulkTeacherAttendanceRequest request)
        {
            var result = await _attendance.SaveTeacherAttendanceBulkAsync(request);
            return result.Succeeded ? Ok(result.Value) : result.Error!.ToActionResult();
        }
    }
}
