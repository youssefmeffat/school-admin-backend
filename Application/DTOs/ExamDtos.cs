using System.ComponentModel.DataAnnotations;

namespace Application.DTOs
{
    public class ExamCreateDto
    {
        [Required]
        public string? Name { get; set; }

        [Required]
        public int SubjectId { get; set; }

        [Required]
        public int GradeId { get; set; }

        public DateTime? ExamDate { get; set; }

        [Range(0.01, double.MaxValue)]
        public decimal? MaxScore { get; set; }
    }

    public class ExamDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }

        public int SubjectId { get; set; }
        public string? SubjectName { get; set; }

        public int GradeId { get; set; }
        public string? GradeName { get; set; }

        public DateTime? ExamDate { get; set; }
        public decimal? MaxScore { get; set; }

        public string Status { get; set; } = "Unscheduled";
    }
}
