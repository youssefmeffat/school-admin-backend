using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface IGradeSubjectsService
    {
        Task<ServiceResult<List<GradeSubjectDto>>> GetForGradeAsync(int gradeId);
        Task<ServiceResult<GradeSubjectDto>> AddAsync(GradeSubjectRequest request);
        Task<ServiceResult> RemoveAsync(int id);
    }
}
