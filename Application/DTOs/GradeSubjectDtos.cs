namespace Application.DTOs
{
    public class GradeSubjectDto
    {
        public int Id { get; set; }
        public int GradeId { get; set; }
        public int SubjectId { get; set; }
    }

    public class GradeSubjectRequest
    {
        public int GradeId { get; set; }
        public int SubjectId { get; set; }
    }
}
