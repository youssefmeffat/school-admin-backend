namespace Application.DTOs
{
    public class ClassSummaryDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }
        public int GradeId { get; set; }
        public int? SchoolId { get; set; }
        public List<ClassStudentRefDto> Students { get; set; } = new();
    }

    public class ClassStudentRefDto
    {
        public int Id { get; set; }
        public string? FullName { get; set; }
    }

    public class UpdateClassStudentsRequest
    {
        public List<int> StudentIds { get; set; } = new();
    }
}
