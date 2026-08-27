using Core.Enums;

namespace Application.DTOs
{
    public class AttendanceRecordDto
    {
        public int Id { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime UpdatedAt { get; set; }
    }

    public class StudentAttendanceListItemDto
    {
        public int StudentId { get; set; }
        public string? StudentName { get; set; }
        public string? StudentCode { get; set; }
        public int? ClassId { get; set; }
        public string? ClassName { get; set; }
        public int? GradeId { get; set; }
        public string? GradeName { get; set; }
        public AttendanceRecordDto? Attendance { get; set; }
    }

    public class StudentAttendanceHistoryDto
    {
        public int Id { get; set; }
        public int StudentId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime UpdatedAt { get; set; }
    }

    public class SavedStudentAttendanceDto
    {
        public int Id { get; set; }
        public int StudentId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime UpdatedAt { get; set; }
    }

    public class TeacherAttendanceListItemDto
    {
        public int TeacherId { get; set; }
        public string? TeacherName { get; set; }
        public string? TeacherCode { get; set; }
        public string? Phone { get; set; }
        public string? Email { get; set; }
        public AttendanceRecordDto? Attendance { get; set; }
    }

    public class TeacherAttendanceHistoryDto
    {
        public int Id { get; set; }
        public int TeacherId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime UpdatedAt { get; set; }
    }

    public class SavedTeacherAttendanceDto
    {
        public int Id { get; set; }
        public int TeacherId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
        public DateTime CreatedAt { get; set; }
        public DateTime UpdatedAt { get; set; }
    }

    public class GenerateAttendanceResultDto
    {
        public DateTime Date { get; set; }
        public int Created { get; set; }
        public int Existing { get; set; }
    }

    public class BulkAttendanceSaveResultDto
    {
        public string Message { get; set; } = "";
        public DateTime Date { get; set; }
    }

    public class StudentAttendanceRequest
    {
        public int StudentId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
    }

    public class BulkStudentAttendanceRequest
    {
        public DateTime Date { get; set; }
        public List<StudentAttendanceRecordRequest> Records { get; set; } = new();
    }

    public class StudentAttendanceRecordRequest
    {
        public int StudentId { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
    }

    public class TeacherAttendanceRequest
    {
        public int TeacherId { get; set; }
        public DateTime Date { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
    }

    public class BulkTeacherAttendanceRequest
    {
        public DateTime Date { get; set; }
        public List<TeacherAttendanceRecordRequest> Records { get; set; } = new();
    }

    public class TeacherAttendanceRecordRequest
    {
        public int TeacherId { get; set; }
        public AttendanceStatus Status { get; set; }
        public TimeSpan? CheckInTime { get; set; }
        public TimeSpan? CheckOutTime { get; set; }
        public string? Notes { get; set; }
        public int? RecordedBy { get; set; }
    }

    public class GenerateAttendanceRequest
    {
        public DateTime Date { get; set; }
        public int? GradeId { get; set; }
        public int? ClassId { get; set; }
    }
}
