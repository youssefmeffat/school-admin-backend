using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface ISubjectsService
    {
        Task<PagedResult<SubjectDto>> GetAllAsync(PaginationParams pagination);
        Task<ServiceResult<SubjectDto>> GetAsync(int id);
        Task<SubjectDto> CreateAsync(SubjectCreateDto dto);
        Task<ServiceResult> UpdateAsync(int id, SubjectCreateDto dto);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
