namespace Application.DTOs
{
    public class TeachingAssignmentCreateDto
    {
        public int TeacherId { get; set; }
        public int SubjectId { get; set; }
        public int ClassId { get; set; }
    }

    public class TeachingAssignmentDto
    {
        public int Id { get; set; }
        public int TeacherId { get; set; }
        public string? TeacherName { get; set; }
        public int SubjectId { get; set; }
        public string? SubjectName { get; set; }
        public int ClassId { get; set; }
        public string? ClassName { get; set; }
        public int GradeId { get; set; }
        public string? GradeName { get; set; }
    }

    public class AvailableClassDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }
        public int GradeId { get; set; }
        public string? GradeName { get; set; }
    }
}
