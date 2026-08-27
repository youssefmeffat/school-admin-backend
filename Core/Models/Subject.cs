using System.Collections.Generic;

namespace Core.Models
{
    public class Subject
    {
        public int Id { get; set; }
        public string? Name { get; set; }
        public string? Code { get; set; }
        public string? Description { get; set; }

        // Relations
        public List<GradeSubject> GradeSubjects { get; set; } = new();
        public List<Exam> Exams { get; set; } = new();
        public List<TeacherSubjectGrade> TeacherSubjectGrades { get; set; } = new();
    }
}
