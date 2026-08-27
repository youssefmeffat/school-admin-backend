using Application.Common;
using Application.DTOs;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class TeachersService : ITeachersService
    {
        private readonly SchoolDbContext _db;

        public TeachersService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<TeacherDto>> GetAllAsync(
            PaginationParams pagination,
            string? search = null)
        {
            var query = _db.Teachers
                .AsNoTracking()
                .AsQueryable();

            if (!string.IsNullOrWhiteSpace(search))
            {
                var term = search.Trim();
                var pattern = $"%{term}%";

                query = query.Where(t =>
                    EF.Functions.Like(t.FullName, pattern) ||
                    (t.Email != null && EF.Functions.Like(t.Email, pattern)));
            }

            var totalCount = await query.CountAsync();

            var items = await query
                .OrderBy(t => t.Id)
                .Select(t => new TeacherDto
                {
                    Id = t.Id,
                    FullName = t.FullName,
                    Email = t.Email
                })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<TeacherDto>.Create(
                items,
                pagination.Page,
                pagination.PageSize,
                totalCount);
        }

        public async Task<ServiceResult<TeacherDto>> GetAsync(int id)
        {
            var t = await _db.Teachers.FindAsync(id);
            if (t == null)
                return ServiceResult<TeacherDto>.Fail(ServiceError.NotFound());

            return ServiceResult<TeacherDto>.Success(
                new TeacherDto
                {
                    Id = t.Id,
                    FullName = t.FullName,
                    Email = t.Email
                });
        }

        public async Task<TeacherDto> CreateAsync(TeacherCreateDto dto)
        {
            var teacher = new Teacher
            {
                FullName = dto.FullName,
                Email = dto.Email,
                Code = dto.Code
            };

            _db.Teachers.Add(teacher);
            await _db.SaveChangesAsync();

            return new TeacherDto
            {
                Id = teacher.Id,
                FullName = teacher.FullName,
                Email = teacher.Email
            };
        }

        public async Task<ServiceResult> UpdateAsync(int id, TeacherCreateDto dto)
        {
            var teacher = await _db.Teachers.FirstOrDefaultAsync(t => t.Id == id);
            if (teacher == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            teacher.FullName = dto.FullName;
            teacher.Email = dto.Email;
            teacher.Code = dto.Code;

            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var teacher = await _db.Teachers.FindAsync(id);
            if (teacher == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Teachers.Remove(teacher);
            await _db.SaveChangesAsync();

            return ServiceResult.Success();
        }
    }
}
