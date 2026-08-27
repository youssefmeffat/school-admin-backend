namespace Core.Models
{
    public class StudentExamResult
    {
        public int Id { get; set; }

        public int StudentId { get; set; }
        public Student? Student { get; set; }

        public int ExamId { get; set; }
        public Exam? Exam { get; set; }

        public decimal? Score { get; set; }
        public string? Notes { get; set; }
    }
}
