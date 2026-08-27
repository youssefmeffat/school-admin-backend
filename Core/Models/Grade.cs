namespace Core.Models
{
    public class Grade
    {
        // Grade level (e.g. Grade 1, Grade 2)
        public int Id { get; set; }
        public string? Name { get; set; }
        public int Number { get; set; }
        public string? Description { get; set; }

        // Navigation
        public List<Class> Classes { get; set; } = new();
        public List<GradeSubject> GradeSubjects { get; set; } = new();
        public List<TeacherSubjectGrade> TeacherSubjectGrades { get; set; } = new();
    }
}
