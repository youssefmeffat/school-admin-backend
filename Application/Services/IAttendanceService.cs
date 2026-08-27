using Application.Common;
using Application.DTOs;

namespace Application.Services
{
    public interface IAttendanceService
    {
        Task<List<StudentAttendanceListItemDto>> GetStudentAttendanceAsync(
            DateTime date, int? studentId, int? gradeId, int? classId);

        Task<ServiceResult<List<StudentAttendanceHistoryDto>>> GetStudentHistoryAsync(
            int studentId, DateTime? from, DateTime? to);

        Task<ServiceResult<SavedStudentAttendanceDto>> SaveStudentAttendanceAsync(
            StudentAttendanceRequest request);

        Task<GenerateAttendanceResultDto> GenerateStudentAttendanceAsync(
            GenerateAttendanceRequest request);

        Task<ServiceResult<BulkAttendanceSaveResultDto>> SaveStudentAttendanceBulkAsync(
            BulkStudentAttendanceRequest request);

        Task<List<TeacherAttendanceListItemDto>> GetTeacherAttendanceAsync(
            DateTime date, int? teacherId);

        Task<ServiceResult<List<TeacherAttendanceHistoryDto>>> GetTeacherHistoryAsync(
            int teacherId, DateTime? from, DateTime? to);

        Task<ServiceResult<SavedTeacherAttendanceDto>> SaveTeacherAttendanceAsync(
            TeacherAttendanceRequest request);

        Task<GenerateAttendanceResultDto> GenerateTeacherAttendanceAsync(
            GenerateAttendanceRequest request);

        Task<ServiceResult<BulkAttendanceSaveResultDto>> SaveTeacherAttendanceBulkAsync(
            BulkTeacherAttendanceRequest request);
    }
}
