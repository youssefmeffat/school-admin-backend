using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class SchoolClassesService : ISchoolClassesService
    {
        private readonly SchoolDbContext _db;

        public SchoolClassesService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<ClassSummaryDto>> GetAllAsync(
            PaginationParams pagination,
            string? search = null)
        {
            var query = _db.Classes
                .AsNoTracking()
                .AsQueryable();

            if (!string.IsNullOrWhiteSpace(search))
            {
                var term = search.Trim();
                var pattern = $"%{term}%";

                query = query.Where(c =>
                    EF.Functions.Like(c.Name, pattern) ||
                    c.Students.Any(s => EF.Functions.Like(s.FullName, pattern)));
            }

            var totalCount = await query.CountAsync();

            var items = await query
                .OrderBy(c => c.Id)
                .Select(c => new ClassSummaryDto
                {
                    Id = c.Id,
                    Name = c.Name,
                    GradeId = c.GradeId,
                    SchoolId = c.SchoolId,
                    Students = c.Students
                        .OrderBy(s => s.FullName)
                        .Select(s => new ClassStudentRefDto
                        {
                            Id = s.Id,
                            FullName = s.FullName
                        })
                        .ToList()
                })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<ClassSummaryDto>.Create(
                items,
                pagination.Page,
                pagination.PageSize,
                totalCount);
        }

        public async Task<ServiceResult<ClassSummaryDto>> GetAsync(int id)
        {
            var cls = await _db.Classes
                .Include(c => c.Students)
                .Where(c => c.Id == id)
                .Select(c => new ClassSummaryDto
                {
                    Id = c.Id,
                    Name = c.Name,
                    GradeId = c.GradeId,
                    SchoolId = c.SchoolId,
                    Students = c.Students.Select(s => new ClassStudentRefDto { Id = s.Id, FullName = s.FullName }).ToList()
                })
                .FirstOrDefaultAsync();

            if (cls == null)
                return ServiceResult<ClassSummaryDto>.Fail(ServiceError.NotFound());

            return ServiceResult<ClassSummaryDto>.Success(cls);
        }

        public async Task<Class> CreateAsync(Class cls)
        {
            _db.Classes.Add(cls);
            await _db.SaveChangesAsync();
            return cls;
        }

        public async Task<ServiceResult> UpdateAsync(int id, Class updated)
        {
            var cls = await _db.Classes.FindAsync(id);
            if (cls == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            cls.Name = updated.Name;
            cls.GradeId = updated.GradeId;
            cls.SchoolId = updated.SchoolId;

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> UpdateStudentsAsync(int id, UpdateClassStudentsRequest request)
        {
            if (!await _db.Classes.AnyAsync(c => c.Id == id))
                return ServiceResult.Fail(ServiceError.NotFound());

            var selectedIds = request.StudentIds?.Distinct().ToHashSet() ?? new HashSet<int>();

            var students = await _db.Students.ToListAsync();

            foreach (var student in students)
            {
                if (student.ClassId == id && !selectedIds.Contains(student.Id))
                    student.ClassId = null;
                else if (selectedIds.Contains(student.Id))
                    student.ClassId = id;
            }

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var cls = await _db.Classes.FindAsync(id);
            if (cls == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Classes.Remove(cls);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}
