using Application.Common;
using Application.DTOs;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class SubjectsService : ISubjectsService
    {
        private readonly SchoolDbContext _db;

        public SubjectsService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<PagedResult<SubjectDto>> GetAllAsync(PaginationParams pagination)
        {
            var totalCount = await _db.Subjects.CountAsync();

            var items = await _db.Subjects
                .OrderBy(s => s.Id)
                .Select(s => new SubjectDto { Id = s.Id, Name = s.Name })
                .Skip(pagination.Skip)
                .Take(pagination.PageSize)
                .ToListAsync();

            return PagedResult<SubjectDto>.Create(items, pagination.Page, pagination.PageSize, totalCount);
        }

        public async Task<ServiceResult<SubjectDto>> GetAsync(int id)
        {
            var s = await _db.Subjects.FindAsync(id);
            if (s == null)
                return ServiceResult<SubjectDto>.Fail(ServiceError.NotFound());

            return ServiceResult<SubjectDto>.Success(new SubjectDto { Id = s.Id, Name = s.Name });
        }

        public async Task<SubjectDto> CreateAsync(SubjectCreateDto dto)
        {
            var subject = new Core.Models.Subject { Name = dto.Name };
            _db.Subjects.Add(subject);
            await _db.SaveChangesAsync();
            return new SubjectDto { Id = subject.Id, Name = subject.Name };
        }

        public async Task<ServiceResult> UpdateAsync(int id, SubjectCreateDto dto)
        {
            var s = await _db.Subjects.FindAsync(id);
            if (s == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            s.Name = dto.Name;
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }

        public async Task<ServiceResult> DeleteAsync(int id)
        {
            var s = await _db.Subjects.FindAsync(id);
            if (s == null)
                return ServiceResult.Fail(ServiceError.NotFound());

            _db.Subjects.Remove(s);
            await _db.SaveChangesAsync();
            return ServiceResult.Success();
        }
    }
}
