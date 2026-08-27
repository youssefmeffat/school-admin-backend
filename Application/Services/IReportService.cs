using Application.DTOs;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Application.Services
{
    public interface IReportService
    {
        Task<IEnumerable<GradeDto>> GetStudentTranscriptAsync(int studentId);
        Task<Dictionary<int, decimal>> GetClassSubjectAveragesAsync(int classId);
    }
}
