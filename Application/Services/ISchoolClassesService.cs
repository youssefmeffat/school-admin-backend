using Application.Common;
using Application.DTOs;
using Core.Models;

namespace Application.Services
{
    public interface ISchoolClassesService
    {
        Task<PagedResult<ClassSummaryDto>> GetAllAsync(PaginationParams pagination, string? search = null);
        Task<ServiceResult<ClassSummaryDto>> GetAsync(int id);
        Task<Class> CreateAsync(Class cls);
        Task<ServiceResult> UpdateAsync(int id, Class updated);
        Task<ServiceResult> UpdateStudentsAsync(int id, UpdateClassStudentsRequest request);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
