using Application.DTOs;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace Application.Services
{
    public class ReportService : IReportService
    {
        private readonly SchoolDbContext _db;

        public ReportService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<IEnumerable<GradeDto>> GetStudentTranscriptAsync(int studentId)
        {
            var grades = await _db.StudentExamResults.Where(g => g.StudentId == studentId).ToListAsync();
            return grades.Select(g => new GradeDto { Id = g.Id, StudentId = g.StudentId, ExamId = g.ExamId, Score = g.Score ?? 0 });
        }

        public async Task<Dictionary<int, decimal>> GetClassSubjectAveragesAsync(int classId)
        {
            // For each subject, compute average grade for students in the class
            var studentIds = await _db.Students.Where(s => s.ClassId == classId).Select(s => s.Id).ToListAsync();
            var subjectAverages = await _db.StudentExamResults
                .Where(g => studentIds.Contains(g.StudentId))
                .GroupBy(g => g.Exam.SubjectId)
                .Select(grp => new { SubjectId = grp.Key, Avg = grp.Average(g => g.Score ?? 0) })
                .ToListAsync();

            return subjectAverages.ToDictionary(x => x.SubjectId, x => (decimal)x.Avg);
        }
    }
}
