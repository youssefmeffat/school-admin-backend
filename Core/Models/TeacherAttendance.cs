using System;
using Core.Enums;

namespace Core.Models
{
    public class TeacherAttendance
    {
        public int Id { get; set; }

        public int TeacherId { get; set; }
        public Teacher Teacher { get; set; } = null!;

        public DateTime Date { get; set; }

        public AttendanceStatus Status { get; set; }

        public TimeSpan? CheckInTime { get; set; }

        public TimeSpan? CheckOutTime { get; set; }

        public string? Notes { get; set; }

        public int? RecordedBy { get; set; }

        public DateTime CreatedAt { get; set; }

        public DateTime UpdatedAt { get; set; }
    }
}