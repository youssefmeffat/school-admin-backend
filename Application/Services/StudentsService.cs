using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class StudentsService : IStudentsService
    {
        private readonly SchoolDbContext _db;

        public StudentsService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<StudentSummaryDto>> GetAllAsync(PaginationParams pagination, string? search = null)
        {
            var query = _db.Students
                .AsNoTracking()
                .Include(s => s.Class)
                .AsQueryable();

            if (!string.IsNullOrWhiteSpace(search))
            {
                var term = search.Trim();
                query = query.Where(s =>
                    EF.Functions.Like(s.FullName, $"%{term}%") ||
                    EF.Functions.Like(s.Code, $"%{term}%") ||
                    (s.Class != null && EF.Functions.Like(s.Class.Name, $"%{term}%")));
            }

            var totalCount = await query.CountAsync();

            var items = await query
                .OrderBy(s => s.Id)
                .Select(s => new StudentSummaryDto
                {
                    Id = s.Id,
                    FullName = s.FullName,
                    Code = s.Code,
                    DateOfBirth = s.DateOfBirth,
                    EnrollDate = s.EnrollDate,
                    ClassId = s.ClassId,
                    Class = s.Class == null ? null : new StudentClassRefDto { Id = s.Class.Id, Name = s.Class.Name }
                })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<StudentSummaryDto>.Create(items, pagination.Page, pagination.PageSize, totalCount);
        }

        public async Task<ServiceResult<StudentSummaryDto>> GetAsync(int id)
        {
            var student = await _db.Students
                .Include(s => s.Class)
                .Where(s => s.Id == id)
                .Select(s => new StudentSummaryDto
                {
                    Id = s.Id,
                    FullName = s.FullName,
                    Code = s.Code,
                    DateOfBirth = s.DateOfBirth,
                    EnrollDate = s.EnrollDate,
                    ClassId = s.ClassId,
                    Class = s.Class == null ? null : new StudentClassRefDto { Id = s.Class.Id, Name = s.Class.Name }
                })
                .FirstOrDefaultAsync();

            if (student == null)
                return ServiceResult<StudentSummaryDto>.Fail(ServiceError.NotFound());

            return ServiceResult<StudentSummaryDto>.Success(student);
        }

        public async Task<ServiceResult<Student>> CreateAsync(Student student)
        {
            if (student.DateOfBirth.HasValue &&
                student.EnrollDate.HasValue &&
                student.EnrollDate.Value.Date < student.DateOfBirth.Value.Date)
            {
                return ServiceResult<Student>.Fail(
                    ServiceError.Validation(
                        "Enrollment date cannot be earlier than date of birth."));
            }

            var code = student.Code.Trim();

            var codeTaken = await _db.Students
                .AnyAsync(s => s.Code == code);

            if (codeTaken)
            {
                return ServiceResult<Student>.Fail(
                    ServiceError.Validation(
                        "Student code is already in use."));
            }

            student.Code = code;

            _db.Students.Add(student);
            await _db.SaveChangesAsync();

            return ServiceResult<Student>.Success(student);
        }

        public async Task<ServiceResult> UpdateAsync(int id, Student updated)
        {
            var student = await _db.Students.FindAsync(id);
            if (student == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            if (updated.DateOfBirth.HasValue &&
                updated.EnrollDate.HasValue &&
                updated.EnrollDate.Value.Date < updated.DateOfBirth.Value.Date)
            {
                return ServiceResult.Fail(
                    ServiceError.Validation(
                        "Enrollment date cannot be earlier than date of birth."));
            }

            var code = updated.Code.Trim();

            var codeTaken = await _db.Students
                .AnyAsync(s =>
                    s.Id != id &&
                    s.Code == code);

            if (codeTaken)
            {
                return ServiceResult.Fail(
                    ServiceError.Validation(
                        "Student code is already in use."));
            }

            student.FullName = updated.FullName;
            student.Code = code;
            student.DateOfBirth = updated.DateOfBirth;
            student.EnrollDate = updated.EnrollDate;
            student.ClassId = updated.ClassId;

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var student = await _db.Students.FindAsync(id);
            if (student == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Students.Remove(student);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}