using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class GradesService : IGradesService
    {
        private readonly SchoolDbContext _db;

        public GradesService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<GradeLevelDto>> GetAllAsync(
            PaginationParams pagination,
            string? search = null)
        {
            var query = _db.Grades
                .AsNoTracking()
                .AsQueryable();

            if (!string.IsNullOrWhiteSpace(search))
            {
                var term = search.Trim();
                var pattern = $"%{term}%";

                query = query.Where(g =>
                    EF.Functions.Like(g.Name, pattern) ||
                    (g.Description != null && EF.Functions.Like(g.Description, pattern)) ||
                    g.Number.ToString() == term);
            }

            var totalCount = await query.CountAsync();

            var items = await query
                .OrderBy(g => g.Number)
                .ThenBy(g => g.Id)
                .Select(g => new GradeLevelDto
                {
                    Id = g.Id,
                    Name = g.Name,
                    Number = g.Number,
                    Description = g.Description
                })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<GradeLevelDto>.Create(
                items,
                pagination.Page,
                pagination.PageSize,
                totalCount);
        }

        public async Task<ServiceResult<GradeLevelDto>> GetAsync(int id)
        {
            var grade = await _db.Grades
                .Where(g => g.Id == id)
                .Select(g => new GradeLevelDto
                {
                    Id = g.Id,
                    Name = g.Name,
                    Number = g.Number,
                    Description = g.Description
                })
                .FirstOrDefaultAsync();

            if (grade == null)
                return ServiceResult<GradeLevelDto>.Fail(ServiceError.NotFound());

            return ServiceResult<GradeLevelDto>.Success(grade);
        }

        public async Task<Grade> CreateAsync(Grade grade)
        {
            _db.Grades.Add(grade);
            await _db.SaveChangesAsync();
            return grade;
        }

        public async Task<ServiceResult> UpdateAsync(int id, Grade updated)
        {
            var grade = await _db.Grades.FindAsync(id);
            if (grade == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            grade.Name = updated.Name;
            grade.Number = updated.Number;
            grade.Description = updated.Description;

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var grade = await _db.Grades.FindAsync(id);
            if (grade == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Grades.Remove(grade);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}