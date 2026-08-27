using System;
using Core.Enums;

namespace Core.Models
{
    public class StudentAttendance
    {
        public int Id { get; set; }

        public int StudentId { get; set; }
        public Student Student { get; set; } = null!;

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