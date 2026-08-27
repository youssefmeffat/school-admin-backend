using Application.DTOs;
using Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    [Produces("application/json")]
    public class ReportsController : ControllerBase
    {
        private readonly IReportService _report;

        public ReportsController(IReportService report)
        {
            _report = report;
        }

        [HttpGet("student/{studentId:int}/transcript")]
        [ProducesResponseType(typeof(IEnumerable<GradeDto>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetTranscript(int studentId)
        {
            var transcript = await _report.GetStudentTranscriptAsync(studentId);
            return Ok(transcript);
        }

        [HttpGet("class/{classId:int}/subject-averages")]
        [ProducesResponseType(typeof(Dictionary<int, decimal>), StatusCodes.Status200OK)]
        public async Task<IActionResult> GetClassSubjectAverages(int classId)
        {
            var avgs = await _report.GetClassSubjectAveragesAsync(classId);
            return Ok(avgs);
        }
    }
}
