using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface ITeachingAssignmentsService
    {
        Task<PagedResult<TeachingAssignmentDto>> GetAllAsync(PaginationParams pagination);
        Task<ServiceResult<List<TeachingAssignmentDto>>> GetForTeacherAsync(int teacherId);
        Task<ServiceResult<List<TeachingAssignmentDto>>> GetForSubjectAsync(int subjectId);
        Task<ServiceResult<List<AvailableClassDto>>> GetAvailableClassesAsync(int subjectId);
        Task<ServiceResult<TeachingAssignmentDto>> CreateAsync(TeachingAssignmentCreateDto dto);
        Task<ServiceResult> DeleteAsync(int id);
    }
}
