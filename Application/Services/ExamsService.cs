using Application.Common;
using Application.DTOs;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class ExamsService : IExamsService
    {
        private readonly SchoolDbContext _db;

        public ExamsService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<ExamDto>> GetAllAsync(
            PaginationParams pagination,
            string? search = null,
            string? status = null)
        {
            var query = _db.Exams
                .AsNoTracking()
                .Include(e => e.Subject)
                .Include(e => e.Grade)
                .AsQueryable();

            if (!string.IsNullOrWhiteSpace(search))
            {
                var term = search.Trim();
                var pattern = $"%{term}%";

                query = query.Where(e =>
                    (e.Name != null && EF.Functions.Like(e.Name, pattern)) ||
                    (e.Subject != null && EF.Functions.Like(e.Subject.Name, pattern)) ||
                    (e.Grade != null && EF.Functions.Like(e.Grade.Name, pattern)));
            }

            if (!string.IsNullOrWhiteSpace(status) &&
                !string.Equals(status, "All", StringComparison.OrdinalIgnoreCase))
            {
                var normalizedStatus = status.Trim();

                query = query.Where(e =>
                    e.Status == normalizedStatus);
            }

            var totalCount = await query.CountAsync();

            var items = await query
                .OrderBy(e => e.ExamDate)
                .ThenBy(e => e.Name)
                .ThenBy(e => e.Id)
                .Select(e => new ExamDto
                {
                    Id = e.Id,
                    Name = e.Name,
                    SubjectId = e.SubjectId,
                    SubjectName = e.Subject != null ? e.Subject.Name : null,
                    GradeId = e.GradeId,
                    GradeName = e.Grade != null ? e.Grade.Name : null,
                    ExamDate = e.ExamDate,
                    MaxScore = e.MaxScore,
                    Status = e.Status
                })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<ExamDto>.Create(
                items,
                pagination.Page,
                pagination.PageSize,
                totalCount);
        }

        public async Task<ServiceResult<ExamDto>> GetAsync(int id)
        {
            var exam = await _db.Exams
                .Include(e => e.Subject)
                .Include(e => e.Grade)
                .Where(e => e.Id == id)
                .Select(e => new ExamDto
                {
                    Id = e.Id,
                    Name = e.Name,

                    SubjectId = e.SubjectId,
                    SubjectName = e.Subject != null ? e.Subject.Name : null,

                    GradeId = e.GradeId,
                    GradeName = e.Grade != null ? e.Grade.Name : null,

                    ExamDate = e.ExamDate,
                    MaxScore = e.MaxScore,
                    Status = e.Status
                })
                .FirstOrDefaultAsync();

            if (exam == null)
                return ServiceResult<ExamDto>.Fail(ServiceError.NotFound());

            return ServiceResult<ExamDto>.Success(exam);
        }

        public async Task<ServiceResult<ExamDto>> CreateAsync(ExamCreateDto dto)
        {
            if (dto.ExamDate.HasValue &&
                dto.ExamDate.Value.Date < DateTime.Today)
            {
                return ServiceResult<ExamDto>.Fail(
                    ServiceError.Validation(
                        "Exam date cannot be in the past."));
            }

            var subjectExists = await _db.Subjects.AnyAsync(s => s.Id == dto.SubjectId);
            if (!subjectExists)
                return ServiceResult<ExamDto>.Fail(ServiceError.Validation("Subject does not exist."));

            var gradeExists = await _db.Grades.AnyAsync(g => g.Id == dto.GradeId);
            if (!gradeExists)
                return ServiceResult<ExamDto>.Fail(ServiceError.Validation("Grade does not exist."));

            var subjectAvailableForGrade = await _db.GradeSubjects.AnyAsync(gs =>
                gs.SubjectId == dto.SubjectId &&
                gs.GradeId == dto.GradeId);

            if (!subjectAvailableForGrade)
                return ServiceResult<ExamDto>.Fail(ServiceError.Validation("This subject is not assigned to the selected grade."));

            var exam = new Core.Models.Exam
            {
                Name = dto.Name?.Trim(),
                SubjectId = dto.SubjectId,
                GradeId = dto.GradeId,
                ExamDate = dto.ExamDate,
                MaxScore = dto.MaxScore,
                Status = GetStatusForDate(dto.ExamDate)
            };

            _db.Exams.Add(exam);
            await _db.SaveChangesAsync();

            return ServiceResult<ExamDto>.Success(new ExamDto
            {
                Id = exam.Id,
                Name = exam.Name,
                SubjectId = exam.SubjectId,
                GradeId = exam.GradeId,
                ExamDate = exam.ExamDate,
                MaxScore = exam.MaxScore,
                Status = exam.Status
            });
        }

        public async Task<ServiceResult> UpdateAsync(int id, ExamCreateDto dto)
        {
            var exam = await _db.Exams.FindAsync(id);
            if (exam == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            if (dto.ExamDate.HasValue &&
                dto.ExamDate.Value.Date < DateTime.Today)
            {
                return ServiceResult.Fail(
                    ServiceError.Validation(
                        "Exam date cannot be in the past."));
            }

            var subjectExists = await _db.Subjects.AnyAsync(s => s.Id == dto.SubjectId);
            if (!subjectExists)
                return ServiceResult.Fail(ServiceError.Validation("Subject does not exist."));

            var gradeExists = await _db.Grades.AnyAsync(g => g.Id == dto.GradeId);
            if (!gradeExists)
                return ServiceResult.Fail(ServiceError.Validation("Grade does not exist."));

            var subjectAvailableForGrade = await _db.GradeSubjects.AnyAsync(gs =>
                gs.SubjectId == dto.SubjectId &&
                gs.GradeId == dto.GradeId);

            if (!subjectAvailableForGrade)
                return ServiceResult.Fail(ServiceError.Validation("This subject is not assigned to the selected grade."));

            exam.Name = dto.Name?.Trim();
            exam.SubjectId = dto.SubjectId;
            exam.GradeId = dto.GradeId;
            exam.ExamDate = dto.ExamDate;
            exam.MaxScore = dto.MaxScore;
            exam.Status = GetStatusForDate(dto.ExamDate);

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        private static string GetStatusForDate(DateTime? examDate)
        {
            if (examDate == null)
                return "Unscheduled";

            return examDate.Value.Date < DateTime.Today
                ? "Completed"
                : "Scheduled";
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var exam = await _db.Exams.FindAsync(id);
            if (exam == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Exams.Remove(exam);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}
