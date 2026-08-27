using Application.Common;
using Application.DTOs;
using Core.Models;

namespace Application.Services
{
    public interface IGradesService
    {
        Task<PagedResult<GradeLevelDto>> GetAllAsync(PaginationParams pagination, string? search = null);
        Task<ServiceResult<GradeLevelDto>> GetAsync(int id);
        Task<ServiceResult<Grade>> CreateAsync(Grade grade);
        Task<ServiceResult> UpdateAsync(int id, Grade updated);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
