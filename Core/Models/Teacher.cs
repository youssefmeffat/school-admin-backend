using System.Collections.Generic;

namespace Core.Models
{
    public class Teacher
    {
        public int Id { get; set; }
        public string? FullName { get; set; }
        public string? Code { get; set; }
        public string? Phone { get; set; }
        public string? Email { get; set; }
        public DateTime? HireDate { get; set; }

        public List<TeacherSubjectGrade> TeacherSubjectGrades { get; set; } = new();
        public List<TeachingAssignment> TeachingAssignments { get; set; } = new();
    }
}
