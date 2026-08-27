namespace Application.DTOs
{
    public class ExamParticipantDto
    {
        public int StudentId { get; set; }

        public string StudentName { get; set; } = "";

        public decimal? Score { get; set; }

        public string? Notes { get; set; }
    }
}
