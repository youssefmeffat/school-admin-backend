using System.ComponentModel.DataAnnotations;

namespace Application.DTOs
{
    public class GradeCreateDto
    {
        [Required]
        public int StudentId { get; set; }
        [Required]
        public int ExamId { get; set; }
        [Range(0, 1000)]
        public decimal Score { get; set; }
    }

    public class GradeDto
    {
        public int Id { get; set; }
        public int StudentId { get; set; }
        public int ExamId { get; set; }
        public decimal Score { get; set; }
    }
}
