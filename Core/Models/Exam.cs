using System.Collections.Generic;

namespace Core.Models
{
    public class Exam
    {
        public int Id { get; set; }
        public string? Name { get; set; }

        public int SubjectId { get; set; }
        public Subject? Subject { get; set; }

        public int GradeId { get; set; }
        public Grade? Grade { get; set; }

        public DateTime? ExamDate { get; set; }
        public decimal? MaxScore { get; set; }

        public string Status { get; set; } = "Unscheduled";

        // Student results
        public List<StudentExamResult> StudentExamResults { get; set; } = new();
    }
}
