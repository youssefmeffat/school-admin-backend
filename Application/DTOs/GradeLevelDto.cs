namespace Application.DTOs
{
    // "Grade" here means grade level (Grade 1, Grade 2, ...), distinct from GradeDto (an exam score).
    public class GradeLevelDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }
        public int Number { get; set; }
        public string? Description { get; set; }
    }
}
