using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class TeachingAssignmentsService : ITeachingAssignmentsService
    {
        private readonly SchoolDbContext _db;

        public TeachingAssignmentsService(SchoolDbContext db)
        {
            _db = db;
        }

        private IQueryable<TeachingAssignmentDto> Query() =>
            from ta in _db.TeachingAssignments
            join teacher in _db.Teachers on ta.TeacherId equals teacher.Id
            join subject in _db.Subjects on ta.SubjectId equals subject.Id
            join cls in _db.Classes on ta.ClassId equals cls.Id
            join grade in _db.Grades on cls.GradeId equals grade.Id
            select new TeachingAssignmentDto
            {
                Id = ta.Id,
                TeacherId = teacher.Id,
                TeacherName = teacher.FullName,
                SubjectId = subject.Id,
                SubjectName = subject.Name,
                ClassId = cls.Id,
                ClassName = cls.Name,
                GradeId = grade.Id,
                GradeName = grade.Name
            };

        public async Task<PagedResult<TeachingAssignmentDto>> GetAllAsync(PaginationParams pagination)
        {
            var totalCount = await _db.TeachingAssignments.CountAsync();

            var items = await Query()
                .OrderBy(x => x.Id)
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<TeachingAssignmentDto>.Create(items, pagination.Page, pagination.PageSize, totalCount);
        }

        public async Task<ServiceResult<List<TeachingAssignmentDto>>> GetForTeacherAsync(int teacherId)
        {
            if (!await _db.Teachers.AnyAsync(t => t.Id == teacherId))
                return ServiceResult<List<TeachingAssignmentDto>>.Fail(ServiceError.NotFound());

            var list = await Query().Where(x => x.TeacherId == teacherId).ToListAsync();
            return ServiceResult<List<TeachingAssignmentDto>>.Success(list);
        }

        public async Task<ServiceResult<List<TeachingAssignmentDto>>> GetForSubjectAsync(int subjectId)
        {
            if (!await _db.Subjects.AnyAsync(s => s.Id == subjectId))
                return ServiceResult<List<TeachingAssignmentDto>>.Fail(ServiceError.NotFound());

            var list = await Query().Where(x => x.SubjectId == subjectId).ToListAsync();
            return ServiceResult<List<TeachingAssignmentDto>>.Success(list);
        }

        public async Task<ServiceResult<List<AvailableClassDto>>> GetAvailableClassesAsync(int subjectId)
        {
            if (!await _db.Subjects.AnyAsync(s => s.Id == subjectId))
                return ServiceResult<List<AvailableClassDto>>.Fail(ServiceError.NotFound("Subject does not exist."));

            var classes = await (
                from cls in _db.Classes
                join gradeSubject in _db.GradeSubjects
                    on cls.GradeId equals gradeSubject.GradeId
                where gradeSubject.SubjectId == subjectId
                select new AvailableClassDto
                {
                    Id = cls.Id,
                    Name = cls.Name,
                    GradeId = cls.GradeId,
                    GradeName = cls.Grade!.Name
                }
            )
            .OrderBy(x => x.GradeId)
            .ThenBy(x => x.Name)
            .ToListAsync();

            return ServiceResult<List<AvailableClassDto>>.Success(classes);
        }

        public async Task<ServiceResult<TeachingAssignmentDto>> CreateAsync(TeachingAssignmentCreateDto dto)
        {
            if (!await _db.Teachers.AnyAsync(t => t.Id == dto.TeacherId))
                return ServiceResult<TeachingAssignmentDto>.Fail(ServiceError.Validation("Teacher does not exist."));

            if (!await _db.Subjects.AnyAsync(s => s.Id == dto.SubjectId))
                return ServiceResult<TeachingAssignmentDto>.Fail(ServiceError.Validation("Subject does not exist."));

            if (!await _db.Classes.AnyAsync(c => c.Id == dto.ClassId))
                return ServiceResult<TeachingAssignmentDto>.Fail(ServiceError.Validation("Class does not exist."));

            var exists = await _db.TeachingAssignments.AnyAsync(x =>
                x.TeacherId == dto.TeacherId &&
                x.SubjectId == dto.SubjectId &&
                x.ClassId == dto.ClassId);

            if (exists)
                return ServiceResult<TeachingAssignmentDto>.Fail(
                    ServiceError.Conflict("This teacher is already assigned to this subject and class."));

            var validClass = await _db.GradeSubjects.AnyAsync(gs =>
                gs.SubjectId == dto.SubjectId &&
                gs.GradeId == _db.Classes
                    .Where(c => c.Id == dto.ClassId)
                    .Select(c => c.GradeId)
                    .First());

            if (!validClass)
                return ServiceResult<TeachingAssignmentDto>.Fail(
                    ServiceError.Validation("This subject is not offered to the grade of the selected class."));

            var assignment = new TeachingAssignment
            {
                TeacherId = dto.TeacherId,
                SubjectId = dto.SubjectId,
                ClassId = dto.ClassId
            };

            _db.TeachingAssignments.Add(assignment);
            await _db.SaveChangesAsync();

            var created = await Query().FirstAsync(x => x.Id == assignment.Id);
            return ServiceResult<TeachingAssignmentDto>.Success(created);
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var assignment = await _db.TeachingAssignments.FindAsync(id);
            if (assignment == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.TeachingAssignments.Remove(assignment);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}
