using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface IExamsService
    {
        Task<PagedResult<ExamDto>> GetAllAsync(
            PaginationParams pagination,
            string? search = null,
            string? status = null);

        Task<ServiceResult<ExamDto>> GetAsync(int id);
        Task<ServiceResult<ExamDto>> CreateAsync(ExamCreateDto dto);
        Task<ServiceResult> UpdateAsync(int id, ExamCreateDto dto);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
