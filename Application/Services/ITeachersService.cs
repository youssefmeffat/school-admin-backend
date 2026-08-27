using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface ITeachersService
    {
        Task<PagedResult<TeacherDto>> GetAllAsync(PaginationParams pagination, string? search = null);
        Task<ServiceResult<TeacherDto>> GetAsync(int id);
        Task<TeacherDto> CreateAsync(TeacherCreateDto dto);
        Task<ServiceResult> UpdateAsync(int id, TeacherCreateDto dto);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
