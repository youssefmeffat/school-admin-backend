namespace Application.DTOs
{
    public class StudentSummaryDto
    {
        public int Id { get; set; }
        public string? FullName { get; set; }
        public string? Code { get; set; }
        public DateTime? DateOfBirth { get; set; }
        public DateTime? EnrollDate { get; set; }
        public int? ClassId { get; set; }
        public StudentClassRefDto? Class { get; set; }
    }

    public class StudentClassRefDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }
    }
}
