using Application.Common;
using Application.DTOs;
using Core.Models;

namespace Application.Services
{
    public interface IStudentsService
    {
        Task<PagedResult<StudentSummaryDto>> GetAllAsync(PaginationParams pagination, string? search = null);
        Task<ServiceResult<StudentSummaryDto>> GetAsync(int id);
        Task<Student> CreateAsync(Student student);
        Task<ServiceResult> UpdateAsync(int id, Student updated);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
