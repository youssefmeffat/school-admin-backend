using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Application.DTOs;

namespace Application.Services
{
    public class StudentExamService : IStudentExamService
    {
        private readonly SchoolDbContext _db;

        public StudentExamService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task EnrollStudentAsync(
            int studentId,
            int examId)
        {
            var student = await _db.Students.FindAsync(studentId);
            var exam = await _db.Exams.FindAsync(examId);

            if (student == null || exam == null)
                throw new KeyNotFoundException(
                    "Student or Exam not found"
                );

            var existing =
                await _db.StudentExamResults
                    .FirstOrDefaultAsync(r =>
                        r.StudentId == studentId &&
                        r.ExamId == examId);

            if (existing != null)
                return;

            var result = new StudentExamResult
            {
                StudentId = studentId,
                ExamId = examId
            };

            _db.StudentExamResults.Add(result);

            await _db.SaveChangesAsync();
        }

        public async Task SetScoreAsync(
            int studentId,
            int examId,
            decimal score)
        {
            var exam =
                await _db.Exams.FindAsync(examId);

            if (exam == null)
                throw new KeyNotFoundException(
                    "Exam not found"
                );

            if (score < 0)
                throw new ArgumentOutOfRangeException(
                    nameof(score),
                    "Score cannot be below 0."
                );

            if (
                exam.MaxScore.HasValue &&
                score > exam.MaxScore.Value
            )
            {
                throw new ArgumentOutOfRangeException(
                    nameof(score),
                    $"Score cannot exceed {exam.MaxScore.Value}."
                );
            }

            var student =
                await _db.Students.FindAsync(studentId);

            if (student == null)
                throw new KeyNotFoundException(
                    "Student not found"
                );

            var result =
                await _db.StudentExamResults
                    .FirstOrDefaultAsync(r =>
                        r.StudentId == studentId &&
                        r.ExamId == examId);

            if (result == null)
            {
                result = new StudentExamResult
                {
                    StudentId = studentId,
                    ExamId = examId,
                    Score = score
                };

                _db.StudentExamResults.Add(result);
            }
            else
            {
                result.Score = score;
            }

            await _db.SaveChangesAsync();
        }

        public async Task<List<ExamParticipantDto>>
        GetExamParticipantsAsync(int examId)
        {
            var exam = await _db.Exams
                .Include(e => e.Grade)
                .FirstOrDefaultAsync(e => e.Id == examId);

            if (exam == null)
                throw new KeyNotFoundException("Exam not found");

            // Get every student whose class belongs
            // to the exam's grade.
            var students = await _db.Students
                .Include(s => s.Class)
                .Where(s =>
                    s.Class != null &&
                    s.Class.GradeId == exam.GradeId
                )
                .OrderBy(s => s.FullName)
                .ToListAsync();

            var studentIds = students
                .Select(s => s.Id)
                .ToList();

            // Get existing results for this exam.
            var existingResults =
                await _db.StudentExamResults
                    .Where(r =>
                        r.ExamId == examId &&
                        studentIds.Contains(r.StudentId)
                    )
                    .ToDictionaryAsync(
                        r => r.StudentId
                    );

            // Return EVERY student, whether they
            // already have a result or not.
            return students
                .Select(student =>
                {
                    existingResults.TryGetValue(
                        student.Id,
                        out var existing
                    );

                    return new ExamParticipantDto
                    {
                        StudentId = student.Id,
                        StudentName = student.FullName,
                        Score = existing?.Score,
                        Notes = existing?.Notes
                    };
                })
                .ToList();
        }

        public async Task<List<StudentExamResult>>
            GetStudentExamsAsync(int studentId)
        {
            return await _db.StudentExamResults
                .Where(se =>
                    se.StudentId == studentId)
                .Include(se => se.Exam)
                .ToListAsync();
        }
    }
}
