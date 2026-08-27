using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class GradeSubjectsService : IGradeSubjectsService
    {
        private readonly SchoolDbContext _db;

        public GradeSubjectsService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<ServiceResult<List<GradeSubjectDto>>> GetForGradeAsync(int gradeId)
        {
            if (!await _db.Grades.AnyAsync(g => g.Id == gradeId))
                return ServiceResult<List<GradeSubjectDto>>.Fail(ServiceError.NotFound());

            var links = await _db.GradeSubjects
                .Where(gs => gs.GradeId == gradeId)
                .Select(gs => new GradeSubjectDto { Id = gs.Id, GradeId = gs.GradeId, SubjectId = gs.SubjectId })
                .ToListAsync();

            return ServiceResult<List<GradeSubjectDto>>.Success(links);
        }

        public async Task<ServiceResult<GradeSubjectDto>> AddAsync(GradeSubjectRequest request)
        {
            if (!await _db.Grades.AnyAsync(g => g.Id == request.GradeId))
                return ServiceResult<GradeSubjectDto>.Fail(ServiceError.Validation("Grade does not exist."));

            if (!await _db.Subjects.AnyAsync(s => s.Id == request.SubjectId))
                return ServiceResult<GradeSubjectDto>.Fail(ServiceError.Validation("Subject does not exist."));

            var existing = await _db.GradeSubjects.FirstOrDefaultAsync(gs =>
                gs.GradeId == request.GradeId && gs.SubjectId == request.SubjectId);

            if (existing != null)
                return ServiceResult<GradeSubjectDto>.Fail(ServiceError.Conflict("Subject is already assigned to this grade."));

            var link = new GradeSubject
            {
                GradeId = request.GradeId,
                SubjectId = request.SubjectId
            };

            _db.GradeSubjects.Add(link);
            await _db.SaveChangesAsync();

            return ServiceResult<GradeSubjectDto>.Success(
                new GradeSubjectDto { Id = link.Id, GradeId = link.GradeId, SubjectId = link.SubjectId });
        }

        public async Task<ServiceResult> RemoveAsync(int id)
        {
            var link = await _db.GradeSubjects.FindAsync(id);
            if (link == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.GradeSubjects.Remove(link);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}
