using System;
using System.Collections.Generic;

namespace Core.Models
{
    public class Student
    {
        public int Id { get; set; }
        public string? FullName { get; set; }
        public string? Code { get; set; }
        public DateTime? DateOfBirth { get; set; }
        public DateTime? EnrollDate { get; set; }

        // Navigation
        public int? ClassId { get; set; }
        public Class? Class { get; set; }

        public List<StudentExamResult> StudentExamResults { get; set; } = new();
    }
}
